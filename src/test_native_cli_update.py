"""Managed native releases: synthetic archives/processes, no device commands."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import native_cli
from native_cli_update import ManagedNativeCLIStore,selection,pointer,version_info
from cli_release_store import API_URL,REPO_URL,ReleaseStoreError
from test_native_cli import zip_bytes,FakeNetwork,FakeRunner,BINARY
from test_cli_release_store import Response,html_asset_row,FakeNetwork as HTMLNetwork

LATEST='2026.10.26'

def metadata(data,value=LATEST):
    name='wuji_'+value+'_x86_64-pc-windows-msvc.zip'
    return dict(tag_name='v'+value,html_url=REPO_URL+'/releases/tag/v'+value,draft=False,prerelease=False,
        assets=[dict(name=name,browser_download_url=REPO_URL+'/releases/download/v'+value+'/'+name,
            digest='sha256:'+hashlib.sha256(data).hexdigest(),size=len(data))])

class Runner(FakeRunner):
    def __init__(self):
        super().__init__();self.outputs['version']='wuji '+LATEST
    def __call__(self,command,**kwargs):
        if command[-3:]==['calib','hand-model','--help']:
            self.calls.append((command,kwargs));return SimpleNamespace(returncode=0,stdout='--sn --timeout-s --jsonl',stderr='')
        return super().__call__(command,**kwargs)

class ManagedNativeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name)/'native';self.data=zip_bytes();self.doc=metadata(self.data)
        self.network=FakeNetwork();self.runner=Runner();self.progress=[]
        self.network.responses[API_URL+'/releases/latest']=json.dumps(self.doc).encode()
        self.network.responses[self.doc['assets'][0]['browser_download_url']]=self.data
        self.store=ManagedNativeCLIStore(self.folder,http_open=self.network,runner=self.runner,progress=self.progress.append,timeout=2)

    def prepared(self):return self.store.prepare(LATEST,release=self.doc,strict=True)

    def test_real_byte_progress_and_four_isolated_software_probes(self):
        current=self.prepared()
        self.assertEqual([p['received_bytes'] for p in self.progress],[0,len(self.data),len(self.data)])
        self.assertEqual([p['stage'] for p in self.progress],['downloading','downloading','verifying'])
        self.assertEqual([c[0][1:] for c in self.runner.calls],[['--version'],['--help'],['viz','--help'],['calib','hand-model','--help']])
        for command,options in self.runner.calls:
            self.assertFalse(options['shell']);self.assertEqual(options['env']['WUJI_NO_UPDATE_CHECK'],'1')
            self.assertNotIn('CODEX_HOME',options['env']);self.assertIn('.prepare-',options['env']['HOME'])
        self.assertEqual(Path(current['binary']).read_bytes(),BINARY)
        self.assertFalse((self.folder/'selection.json').exists())
        self.assertFalse((Path(current['directory'])/'software-check-home').exists())

    def test_only_explicit_stable_published_metadata_is_installable(self):
        for change in ({'prerelease':True},{'draft':True},{'prerelease':None},{'tag_name':'v2026.10.26-beta'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.store.prepare(LATEST,release={**self.doc,**change})
        self.assertEqual(self.network.calls,[]);self.assertEqual(self.runner.calls,[])

    def test_api_discovery_uses_windows_archive(self):
        self.assertEqual(self.store.fetch_release()['tag_name'],'v'+LATEST)
        self.assertEqual(self.progress,[])

    def html_discovery(self, error, *, selected=False):
        network=HTMLNetwork();network.responses.update(self.network.responses)
        self.network=network;self.store.http_open=network
        tag='v'+LATEST
        self.network.responses[API_URL+('/releases/tags/'+tag if selected else '/releases/latest')]=error
        self.network.responses[REPO_URL+('/releases/tag/'+tag if selected else '/releases/latest')]=lambda url:Response(b'<html>Latest</html>',self.doc['html_url'])
        asset=self.doc['assets'][0]
        self.network.responses[REPO_URL+'/releases/expanded_assets/'+tag]=html_asset_row(
            asset['name'],asset['digest'],href=asset['browser_download_url']).encode()

    def test_rate_limited_latest_redirect_keeps_real_stable_windows_updates_usable(self):
        errors=(HTTPError(API_URL,403,'Rate limit',{},None),HTTPError(API_URL,429,'Rate limit',{},None),TimeoutError('Metadata timeout'))
        for error in errors:
            with self.subTest(error=type(error).__name__,code=getattr(error,'code',None)):
                self.html_discovery(error)
                document=self.store.fetch_release()
                self.assertIs(document['prerelease'],False)
                self.assertEqual(document['stability_source'],'github_latest_release_redirect')
                self.assertEqual(document['latest_url'],REPO_URL+'/releases/latest')
                self.assertEqual(document['assets'][0]['digest'],self.doc['assets'][0]['digest'])
                self.assertEqual(self.store.prepare(LATEST,release=document,strict=True)['version'],LATEST)
        self.assertFalse((self.folder/'selection.json').exists())

    def test_bare_tag_html_cannot_claim_stable_even_with_a_latest_label(self):
        self.html_discovery(HTTPError(API_URL,403,'Rate limit',{},None),selected=True)
        with self.assertRaisesRegex(ValueError,'stable'):
            self.store.fetch_release(LATEST)
        self.assertEqual(self.runner.calls,[])
        self.assertFalse(self.store.root.exists())

    def test_latest_without_exact_official_tag_redirect_is_not_stable(self):
        self.html_discovery(HTTPError(API_URL,403,'Rate limit',{},None))
        self.network.responses[REPO_URL+'/releases/latest']=b'<html>Latest</html>'
        with self.assertRaises(ValueError):self.store.fetch_release()
        self.assertEqual(self.runner.calls,[])
        self.assertFalse(self.store.root.exists())

    def test_latest_redirect_cannot_cross_repository_or_add_query_or_encoding(self):
        for final in ('https://github.com/other/wuji-cli/releases/tag/v'+LATEST,
                self.doc['html_url']+'?version=2026.10.12',
                self.doc['html_url']+'%2F..',
                'https://untrusted@github.com/wuji-technology/wuji-cli/releases/tag/v'+LATEST):
            with self.subTest(final=final):
                self.html_discovery(HTTPError(API_URL,403,'Rate limit',{},None))
                self.network.responses[REPO_URL+'/releases/latest']=lambda url:Response(b'<html>Latest</html>',final)
                with self.assertRaises(ValueError):self.store.fetch_release()
        self.assertEqual(self.runner.calls,[])
        self.assertFalse(self.store.root.exists())

    def test_failed_checksum_or_software_probe_never_leaves_version(self):
        bad=copy.deepcopy(self.doc);bad['assets'][0]['digest']='sha256:'+'a'*64
        with self.assertRaises(ValueError):self.store.prepare(LATEST,release=bad)
        self.assertFalse((self.store.root/LATEST).exists());self.assertEqual(self.runner.calls,[])
        self.runner.outputs['version']='wuji 2026.10.12'
        with self.assertRaises(ValueError):self.prepared()
        self.assertFalse((self.store.root/LATEST).exists());self.assertEqual(list(self.store.root.glob('.prepare-*')),[])

    def test_selection_drives_default_resolve_and_prepare_without_network(self):
        current=self.prepared();self.folder.joinpath('selection.json').write_text(json.dumps({'current':pointer(current)}))
        with patch.object(native_cli.NativeCLIStore,'prepare',side_effect=AssertionError('must not use bundled executable')):
            self.assertEqual(native_cli.resolve(folder=self.folder,strict=True),current['binary'])
            self.assertEqual(native_cli.prepare(folder=self.folder,strict=True)['version'],LATEST)
        self.assertEqual(len(self.network.calls),1)

    def test_rewritten_payload_and_manifest_still_fail_archive_binding(self):
        current=self.prepared();path=Path(current['binary']);path.write_bytes(b'replacement')
        m=Path(current['directory'])/'manifest.json';doc=json.loads(m.read_text());digest=hashlib.sha256(path.read_bytes()).hexdigest()
        doc['binary_sha256']=digest;doc['files'][0].update(sha256=digest,size=path.stat().st_size);m.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):version_info(LATEST,folder=self.folder,strict=True)

    def test_selected_identity_mismatch_is_not_silently_replaced_by_pin(self):
        current=self.prepared();row=pointer(current);row['binary_sha256']='a'*64
        self.folder.joinpath('selection.json').write_text(json.dumps({'current':row}))
        with self.assertRaises(ValueError):native_cli.resolve(folder=self.folder,strict=True)

    def test_bad_selection_and_payload_metadata_fail_closed(self):
        self.folder.mkdir();self.folder.joinpath('selection.json').write_text('{')
        with self.assertRaises(ValueError):selection(self.folder)
        self.folder.joinpath('selection.json').unlink();current=self.prepared()
        path=Path(current['directory'])/'manifest.json';doc=json.loads(path.read_text());doc['files']=[3];path.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):version_info(LATEST,folder=self.folder,strict=True)

    def test_help_requires_calibration_arguments(self):
        original=self.runner
        def runner(command,**kwargs):
            if 'hand-model' in command:return SimpleNamespace(returncode=0,stdout='no compatible arguments')
            return original(command,**kwargs)
        self.store.runner=runner
        with self.assertRaises(ValueError):self.prepared()
        self.assertFalse((self.store.root/LATEST).exists())

    def test_exact_old_calibration_source_survives_later_selected_release(self):
        from calibration_portability import calibration_proof
        from test_calibration_portability import completed,MODEL
        current=self.prepared();model=self.folder/'right_hand.urdf';model.write_bytes(MODEL)
        value=completed(model);value['run'].update(cli_version=LATEST,cli_binary_sha256=current['binary_sha256'],cli_archive_sha256=current['archive_sha256'])
        def actual(version,**kwargs):return version_info(version,folder=self.folder,**kwargs)
        with patch('native_cli_update.version_info',side_effect=actual),patch('native_cli.current_info',side_effect=AssertionError('must verify original version')):
            self.assertEqual(calibration_proof(value)['source_cli_version'],LATEST)
            value['run']['cli_binary_sha256']='a'*64
            with self.assertRaises(ValueError):calibration_proof(value)

if __name__=='__main__':unittest.main()
