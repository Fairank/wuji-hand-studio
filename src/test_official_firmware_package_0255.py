"""Full package prepare/validate/failure/retry flow with synthetic ZIP and HTTP."""
from dataclasses import FrozenInstanceError
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError
import warnings
import zipfile

from official_firmware_catalog import ORIGIN,PATH
from official_firmware_package import MAX_UNCOMPRESSED,PackageError,TIMEOUT,prepare_package,validate_package
from test_official_firmware_catalog_0255 import SN,entry


BIN=bytes(range(256))*128
ELF=b'\x7fELF'+bytes(range(256))*64


def bundle(*,members=None,manifest=None,compression=zipfile.ZIP_DEFLATED):
    row=entry()
    row['manifest'].update(size=len(BIN),digest=dict(algorithm='sha256',value=hashlib.sha256(BIN).hexdigest()))
    row.update(elf_size=len(ELF),elf_sha256=hashlib.sha256(ELF).hexdigest())
    base='gboard-v1.0.4'
    normal=[('manifest.json',json.dumps(row['manifest'] if manifest is None else manifest).encode('utf8')),
        (base+'.signed.bin',BIN),(base+'.defmt.elf',ELF)]
    output=io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(output,'w',compression=compression) as archive:
            for name,body in normal if members is None else members:archive.writestr(name,body)
    raw=output.getvalue()
    row.update(file_size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return row,raw


def repin(row,raw):
    row=copy.deepcopy(row);row.update(file_size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return row,raw


class Response(io.BytesIO):
    def __init__(self,raw,url,*,status=200,headers=None):
        super().__init__(raw);self.url,self.status=url,status
        self.headers={'Content-Type':'application/zip',**(headers or {})}
    def getcode(self):return self.status
    def geturl(self):return self.url


class FirmwarePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cache=self.root/'cache'
        self.row,self.raw=bundle()

    def opener(self,raw=None,**options):
        raw=self.raw if raw is None else raw
        return Mock(side_effect=lambda request,**kw:Response(raw,request.full_url,**options))

    def prepare(self,*,row=None,opener=None,progress=None):
        return prepare_package(self.row if row is None else row,SN,'wuji_glove',self.cache,
            opener=self.opener() if opener is None else opener,on_progress=progress)

    def write(self,row,raw):
        path=self.root/'candidate.zip';path.write_bytes(raw)
        return path

    def deny_bundle(self,row,raw):
        path=self.write(row,raw)
        with self.assertRaises(PackageError):validate_package(row,SN,'wuji_glove',path)

    def test_prepare_validate_and_cached_reopen_are_complete_and_not_write_authority(self):
        events=[];opener=self.opener()
        record=self.prepare(opener=opener,progress=events.append)
        self.assertEqual(opener.call_count,1)
        request=opener.call_args.args[0]
        self.assertEqual(request.full_url,ORIGIN+self.row['download_url']);self.assertEqual(request.get_method(),'GET')
        self.assertEqual(opener.call_args.kwargs,dict(timeout=TIMEOUT))
        self.assertEqual(Path(record.path).read_bytes(),self.raw)
        self.assertTrue(record.package_integrity_verified);self.assertFalse(record.hardware_compatibility_verified)
        self.assertEqual(record.serial,SN);self.assertEqual(record.version,'1.0.4')
        self.assertEqual(record,validate_package(self.row,SN,'wuji_glove',record.path))
        with self.assertRaises(FrozenInstanceError):record.version='9.0.0'
        self.assertEqual(events[-1],dict(stage='ready',received_bytes=len(self.raw),total_bytes=len(self.raw)))
        values=[event['received_bytes'] for event in events if event['stage']=='downloading']
        self.assertEqual(values[0],0);self.assertEqual(values[-1],len(self.raw));self.assertEqual(values,sorted(values))
        cached_events=[];blocked=Mock(side_effect=AssertionError('A cache hit must not download'))
        self.assertEqual(self.prepare(opener=blocked,progress=cached_events.append),record)
        blocked.assert_not_called();self.assertEqual(cached_events,[dict(stage='cache_verified',received_bytes=0,total_bytes=len(self.raw))])
        self.assertEqual(list(self.cache.glob('*.part')),[])

    def test_download_offline_partial_and_digest_failure_clean_only_own_partial_then_explicit_retry(self):
        cases=[Mock(side_effect=URLError('synthetic offline')),self.opener(self.raw[:-1]),self.opener(self.raw+b'extra'),self.opener(b'x'*len(self.raw))]
        for opener in cases:
            with self.subTest(case=opener):
                with self.assertRaises(PackageError):self.prepare(opener=opener)
                self.assertEqual(opener.call_count,1)
                self.assertEqual(list(self.cache.iterdir()),[])
        record=self.prepare()
        self.assertEqual(Path(record.path).read_bytes(),self.raw)

    def test_existing_corrupt_cache_is_preserved_without_download_or_overwrite(self):
        record=self.prepare();path=Path(record.path);path.write_bytes(b'corrupt preserved evidence')
        opener=Mock(side_effect=AssertionError('Never overwrite corrupt cached firmware'))
        with self.assertRaises(PackageError):self.prepare(opener=opener)
        self.assertEqual(path.read_bytes(),b'corrupt preserved evidence');opener.assert_not_called()
        with self.assertRaises(PackageError):validate_package(self.row,SN,'wuji_glove',path)

    def test_interrupt_and_disk_sync_failure_leave_no_published_package_or_automatic_retry(self):
        opener=self.opener()
        def cancelled(event):
            if event['received_bytes']:raise PackageError('Synthetic cancel',code='cancelled')
        with self.assertRaises(PackageError):self.prepare(opener=opener,progress=cancelled)
        self.assertEqual(opener.call_count,1);self.assertEqual(list(self.cache.iterdir()),[])
        with patch('official_firmware_package.os.fsync',side_effect=OSError('synthetic disk full')):
            with self.assertRaises(PackageError):self.prepare()
        self.assertEqual(list(self.cache.iterdir()),[])
        self.assertTrue(self.prepare().package_integrity_verified)

    def test_http_redirect_nonzip_status_size_headers_and_timeout_are_not_retried(self):
        url=ORIGIN+self.row['download_url']
        cases=[Mock(side_effect=lambda request,**kw:Response(self.raw,'https://evil.invalid/archive.zip')),
            self.opener(headers={'Content-Type':'text/html'}),self.opener(headers={'Content-Length':'0'}),self.opener(status=302),
            Mock(side_effect=HTTPError(url,404,'Not Found',{},io.BytesIO(b'not found')))]
        for opener in cases:
            with self.subTest(case=opener):
                with self.assertRaises(PackageError):self.prepare(opener=opener)
                self.assertEqual(opener.call_count,1);self.assertEqual(list(self.cache.iterdir()),[])
        opener=self.opener()
        with patch('official_firmware_package.time.monotonic',side_effect=[100.,100.+TIMEOUT+1]):
            with self.assertRaises(PackageError):self.prepare(opener=opener)
        self.assertEqual(opener.call_count,1);self.assertEqual(list(self.cache.iterdir()),[])

    def test_manifest_exact_fields_duplicate_json_and_internal_digests_are_checked(self):
        normal=[('manifest.json',json.dumps(self.row['manifest']).encode()),('gboard-v1.0.4.signed.bin',BIN),('gboard-v1.0.4.defmt.elf',ELF)]
        bad_manifest=copy.deepcopy(self.row['manifest']);bad_manifest['version']='1.0.3'
        cases=[bundle(manifest=bad_manifest),
            bundle(members=[(normal[0][0],normal[0][1].replace(b'"manifest_version": 1',b'"manifest_version":1,"manifest_version":1')),*normal[1:]]),
            bundle(members=[normal[0],(normal[1][0],b'x'+BIN[1:]),normal[2]]),
            bundle(members=[*normal[:2],(normal[2][0],b'x'+ELF[1:])])]
        for row,raw in cases:
            with self.subTest(sha=row['sha256']):self.deny_bundle(row,raw)

    def test_extra_traversal_duplicate_missing_special_and_unsupported_archive_members_rejected(self):
        normal=[('manifest.json',json.dumps(self.row['manifest']).encode()),('gboard-v1.0.4.signed.bin',BIN),('gboard-v1.0.4.defmt.elf',ELF)]
        link=zipfile.ZipInfo(normal[1][0]);link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        cases=[bundle(members=[*normal,('../outside',b'bad')]),bundle(members=[*normal,('tool.exe',b'MZbad')]),
            bundle(members=[*normal,normal[0]]),bundle(members=normal[:2]),bundle(members=[normal[0],(link,BIN),normal[2]]),
            bundle(compression=zipfile.ZIP_BZIP2)]
        for row,raw in cases:
            with self.subTest(sha=row['sha256']):self.deny_bundle(row,raw)
        self.assertFalse((self.root/'outside').exists())
        self.deny_bundle(*repin(self.row,b'MZ'+self.raw))
        self.deny_bundle(*repin(self.row,self.raw+b'trailing executable'))

    def test_raw_manifest_boolean_or_float_cannot_equal_an_integer_field(self):
        for key,value in [('manifest_version',True),('manifest_version',1.0),('size',float(len(BIN)))]:
            with self.subTest(key=key,value=value):
                malformed=copy.deepcopy(self.row['manifest']);malformed[key]=value
                self.deny_bundle(*bundle(manifest=malformed))

    def test_encrypted_and_declared_zip_bomb_are_rejected_before_decompression(self):
        encrypted=bytearray(self.raw);first=encrypted.index(b'PK\x01\x02')
        struct.pack_into('<H',encrypted,first+8,struct.unpack_from('<H',encrypted,first+8)[0]|1)
        self.deny_bundle(*repin(self.row,bytes(encrypted)))
        bomb=bytearray(self.raw);first=bomb.index(b'PK\x01\x02')
        struct.pack_into('<I',bomb,first+24,MAX_UNCOMPRESSED+1)
        self.deny_bundle(*repin(self.row,bytes(bomb)))

    def test_hardlinked_private_package_paths_are_rejected(self):
        original=self.write(self.row,self.raw);linked=self.root/'linked.zip'
        os.link(original,linked)
        with self.assertRaises(PackageError):validate_package(self.row,SN,'wuji_glove',linked)
        linked.unlink()
        self.assertTrue(validate_package(self.row,SN,'wuji_glove',original).package_integrity_verified)

    def test_redirected_private_package_paths_are_rejected(self):
        original=self.write(self.row,self.raw)
        redirected=self.root/'redirected.zip'
        try:redirected.symlink_to(original)
        except OSError:self.skipTest('OS does not allow creating a test symlink')
        with self.assertRaises(PackageError):validate_package(self.row,SN,'wuji_glove',redirected)

    def test_cache_publish_does_not_replace_a_different_existing_file(self):
        def collision(source,destination):
            Path(destination).write_bytes(b'concurrent damaged cache')
            raise FileExistsError('synthetic cache collision')
        with patch('official_firmware_package._publish',side_effect=collision):
            with self.assertRaises(PackageError):self.prepare()
        files=list(self.cache.iterdir());self.assertEqual(len(files),1)
        self.assertEqual(files[0].read_bytes(),b'concurrent damaged cache')

    def test_invalid_metadata_or_relative_cache_path_never_opens_http(self):
        opener=Mock(side_effect=AssertionError('Invalid inputs cannot download'))
        invalid=copy.deepcopy(self.row);invalid['download_url']='https://evil.invalid/firmware.zip'
        with self.assertRaises(PackageError):self.prepare(row=invalid,opener=opener)
        with self.assertRaises(PackageError):prepare_package(self.row,SN,'wuji_glove',Path('relative'),opener=opener)
        opener.assert_not_called()


if __name__=='__main__':unittest.main()
