"""Official metadata pipeline with synthetic HTTP, no devices or network."""
import copy
import io
import json
from unittest import TestCase,main
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError

from official_firmware_catalog import CatalogError,MAX_METADATA,ORIGIN,PATH,TIMEOUT,fetch_catalog,parse_catalog,_NoRedirect

SN='WG1KA00000000000'
ID='11111111-2222-7333-8444-555555555555'


def entry(version='1.0.4',identifier=ID):
    return dict(id=identifier,device_type_code='WG1',version=version,file_size=216639,sha256='a'*64,
        manifest=dict(size=313912,digest=dict(value='b'*64,algorithm='sha256'),version=version,device_type='WG1J',component_id='gboard',
            release_date='2026-10-26T03:45:19Z',release_notes={'en-US':dict(description='Synthetic firmware',update_description='Synthetic notes\nSecond line')},manifest_version=1),
        download_url=PATH+'/'+identifier+'/download?sn='+SN,has_elf=True,elf_size=662676,elf_sha256='c'*64,
        elf_download_url=PATH+'/'+identifier+'/elf?sn='+SN)


def document(rows=None):return dict(code=0,msg='success',data=[entry()] if rows is None else rows)
def encode(value):return json.dumps(value).encode('utf8')


class Response(io.BytesIO):
    def __init__(self,body,url,status=200,headers=None):
        super().__init__(body);self.url,self.status=url,status
        self.headers={'Content-Type':'application/json; charset=utf-8',**(headers or {})}
    def geturl(self):return self.url
    def getcode(self):return self.status


class CatalogTests(TestCase):
    def parse(self,value,current='1.0.4',**options):return parse_catalog(encode(value),SN,'wuji_glove',current,**options)
    def deny(self,value,**options):
        with self.assertRaises(CatalogError):self.parse(value,**options)

    def test_serial_catalog_lists_versions_without_compatibility_or_write_authority(self):
        rows=[entry('0.12.0','11111111-2222-7333-8444-555555555550'),entry(),entry('1.0.3','11111111-2222-7333-8444-555555555553')]
        result=self.parse(document(rows))
        self.assertEqual(result['scope'],'serial_catalog')
        self.assertEqual([row['version'] for row in result['catalog_metadata']['entries']],['1.0.4','1.0.3','0.12.0'])
        self.assertEqual(result['catalog_metadata']['latest_listed'],'1.0.4')
        self.assertFalse(result['catalog_metadata']['higher_listed'])
        self.assertFalse(result['can_upgrade']);self.assertFalse(result['compatibility_verified']);self.assertFalse(result['up_to_date_authority'])
        self.assertEqual(result['catalog_metadata']['entries'][0]['manifest']['device_type'],'WG1J','Family listing must not pretend matching physical hand side')

    def test_numeric_newer_comparison_empty_and_unknown_current_are_distinct(self):
        self.assertTrue(self.parse(document(),'1.0.3')['catalog_metadata']['higher_listed'])
        self.assertFalse(self.parse(document(),'1.0.5')['catalog_metadata']['higher_listed'])
        self.assertIsNone(self.parse(document(),None)['catalog_metadata']['higher_listed'])
        empty=self.parse(document([]))
        self.assertEqual(empty['phase'],'empty');self.assertIsNone(empty['catalog_metadata']['latest_listed'])
        self.assertFalse(empty['up_to_date_authority'])

    def test_http_fetch_uses_one_exact_official_get_no_hw_filter_unless_explicit(self):
        captured=[]
        def opener(request,**options):
            captured.append((request,options));return Response(encode(document()),request.full_url)
        result=fetch_catalog(SN,'wuji_glove','1.0.4',opener=opener)
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0][0].full_url,ORIGIN+PATH+'?sn='+SN)
        self.assertEqual(captured[0][0].get_method(),'GET');self.assertEqual(captured[0][1],dict(timeout=TIMEOUT))
        filtered=fetch_catalog(SN,'wuji_glove','1.0.4',hardware_version='1.0.0',opener=opener)
        self.assertEqual(captured[1][0].full_url,ORIGIN+PATH+'?sn='+SN+'&hw_version=1.0.0')
        self.assertEqual(filtered['scope'],'hardware_filtered_catalog');self.assertFalse(filtered['compatibility_verified'])

    def test_explicit_hardware_not_found_is_display_state_not_latest_or_update(self):
        failure=dict(code=404,error_code='HARDWARE_VERSION_NOT_FOUND',msg='hardware version not found for this device type')
        def opener(request,**options):
            raise HTTPError(request.full_url,404,'Not Found',{'Content-Type':'application/json'},io.BytesIO(encode(failure)))
        state=fetch_catalog(SN,'wuji_glove','1.0.4',hardware_version='1.0.0',opener=opener)
        self.assertEqual(state['phase'],'hardware_unavailable');self.assertEqual(state['error']['code'],'HARDWARE_VERSION_NOT_FOUND')
        self.assertIsNone(state['catalog_metadata']['latest_listed']);self.assertFalse(state['can_upgrade'])
        self.assertFalse(state['up_to_date_authority']);self.assertFalse(state['compatibility_verified'])
        self.deny(failure,http_status=200)
        self.deny(dict(failure,code=True),http_status=404)

    def test_offline_is_not_empty_catalog_and_retry_is_only_a_new_explicit_call(self):
        opener=Mock(side_effect=URLError('synthetic offline'))
        with self.assertRaises(CatalogError) as caught:fetch_catalog(SN,'wuji_glove',opener=opener)
        self.assertEqual(caught.exception.code,'network_unavailable');self.assertEqual(opener.call_count,1)
        opener.side_effect=lambda request,**kw:Response(encode(document()),request.full_url)
        self.assertEqual(fetch_catalog(SN,'wuji_glove',opener=opener)['phase'],'listed')
        self.assertEqual(opener.call_count,2)

    def test_duplicate_fields_nonfinite_truncated_encoding_and_legacy_envelopes_rejected(self):
        bad=[b'{"code":0,"code":0,"msg":"success","data":[]}',b'{"code":0,"msg":"success","data":NaN}',
            b'{"code":0',b'\xff',b'['*1100+b']'*1100,
            encode(dict(devices=[])),encode(dict(code=True,msg='success',data=[])),encode(dict(code=0,msg=None,data=[])),encode(document())+b' extra']
        for raw in bad:
            with self.subTest(raw=raw[:50]):
                with self.assertRaises(CatalogError):parse_catalog(raw,SN,'wuji_glove')
        raw=encode(document()).replace(b'"algorithm": "sha256"',b'"algorithm":"sha256","algorithm":"sha256"')
        with self.assertRaises(CatalogError):parse_catalog(raw,SN,'wuji_glove')

    def test_uuid_type_size_digest_version_component_and_manifest_controls(self):
        changes=[('id','not-a-uuid'),('id','AAAAAAAA-2222-7333-8444-555555555555'),('file_size',True),('file_size',0),('file_size',2**32),
            ('sha256','A'*64),('sha256','a'*63),('device_type_code','WH2'),('version','01.0.4'),('has_elf',1),('elf_size',False),('elf_sha256','bad')]
        for key,bad in changes:
            with self.subTest(key=key,bad=bad):
                row=entry();row[key]=bad;self.deny(document([row]))
        manifest=[('device_type',[]),('device_type','WH2J'),('component_id','other'),('version','1.0.3'),('size',False),('size',0),
            ('manifest_version',True),('manifest_version',2),('release_date','2026-02-30T00:00:00Z'),('release_notes',{}),
            ('digest',dict(algorithm='md5',value='a'*64)),('digest',dict(algorithm='sha256',value='x'*64))]
        for key,bad in manifest:
            with self.subTest(manifest_key=key):
                row=entry();row['manifest'][key]=bad;self.deny(document([row]))
        row=entry();row['manifest']['release_notes']['en-US']['description']='bad\x1btext';self.deny(document([row]))
        row=entry();row['manifest']['release_notes']['en-US']['description']='bad\ud800text';self.deny(document([row]))
        row=entry();row['manifest']['extra']=True;self.deny(document([row]))
        self.deny(document([entry(),entry()]))

    def test_zip_and_component_digests_are_independently_validated_not_equated(self):
        result=self.parse(document())
        row=result['catalog_metadata']['entries'][0]
        self.assertNotEqual(row['sha256'],row['manifest']['digest']['value'])
        row=entry();row['has_elf']=False;row.update(elf_size=None,elf_sha256=None,elf_download_url=None)
        self.assertEqual(self.parse(document([row]))['phase'],'listed')
        row['elf_download_url']='unexpected';self.deny(document([row]))

    def test_urls_bind_exact_uuid_serial_canonical_query_and_relative_origin(self):
        good=entry()['download_url']
        bad=[ORIGIN+good,'https://evil.invalid'+good,'//api.wuji.tech'+good,good+'#fragment',good+'&sn='+SN,
            good+'&extra=1',good.replace(SN,'OTHER'),good.replace('/download','/%64ownload'),good.replace(ID,ID[:-1]+'6'),
            good.replace('/download','/../download'),good.replace('/download','//download'),good+'\n',good.replace('sn=','sn=%57'),
            'https://[',good.replace('?sn=','?%73n='),good.replace('/v2/','/v1/')]
        for url in bad:
            with self.subTest(url=url):
                row=entry();row['download_url']=url;self.deny(document([row]))
        row=entry();row['elf_download_url']=good;self.deny(document([row]))

    def test_http_redirect_wrong_type_lengths_and_oversized_response_fail_without_retry(self):
        target=ORIGIN+PATH+'?sn='+SN
        cases=[dict(url=ORIGIN+'/different'),dict(headers={'Content-Type':'text/html'}),dict(headers={'Content-Length':str(MAX_METADATA+1)}),
            dict(headers={'Content-Length':'-1'}),dict(headers={'Content-Length':'0'}),dict(status=302)]
        for changes in cases:
            with self.subTest(changes=changes):
                kwargs=dict(url=target);kwargs.update(changes)
                opener=Mock(return_value=Response(encode(document()),**kwargs))
                with self.assertRaises(CatalogError):fetch_catalog(SN,'wuji_glove',opener=opener)
                self.assertEqual(opener.call_count,1)
        opener=Mock(return_value=Response(b' '* (MAX_METADATA+1),target))
        with self.assertRaises(CatalogError):fetch_catalog(SN,'wuji_glove',opener=opener)
        self.assertEqual(opener.call_count,1)
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_bad_arguments_do_not_open_http_or_invent_hand_schema(self):
        for sn,kind,current,hw in [('bad?serial','wuji_glove',None,None),(SN,'wuji_hand_2',None,None),
                (SN,'wuji_glove',True,None),(SN,'wuji_glove',None,'1.0'),(SN,'wuji_glove',None,True)]:
            with self.subTest(sn=sn,kind=kind,current=current,hw=hw):
                opener=Mock()
                with self.assertRaises(CatalogError):fetch_catalog(sn,kind,current,hardware_version=hw,opener=opener)
                opener.assert_not_called()

    def test_deadline_expiry_stops_body_read_without_network_retry(self):
        opener=Mock(return_value=Response(encode(document()),ORIGIN+PATH+'?sn='+SN))
        with patch('official_firmware_catalog.time.monotonic',side_effect=[100.,100.+TIMEOUT+1]):
            with self.assertRaises(CatalogError):fetch_catalog(SN,'wuji_glove',opener=opener)
        self.assertEqual(opener.call_count,1)


if __name__=='__main__':main()
