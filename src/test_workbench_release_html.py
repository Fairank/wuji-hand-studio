"""Official HTML fallback contracts, exact lengths and hashes; no network."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from urllib.error import HTTPError
from workbench_release import WorkbenchRelease,REPO_URL,LATEST_URL,MAX_INSTALLER
from test_workbench_release import Response,release,VERSION

class ReleaseHTMLTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.payload=b'MZ SYNTHETIC PUBLIC INSTALLER';self.doc=release(self.payload)
        self.asset=self.doc['assets'][0];self.url=self.asset['browser_download_url'];self.name=self.asset['name']
        self.final=REPO_URL+'/releases/tag/v'+VERSION;self.latest=REPO_URL+'/releases/latest'
        self.expanded=REPO_URL+'/releases/expanded_assets/v'+VERSION
        self.digest=self.asset['digest'];self.calls=[];self.api_status=403
        self.length=str(len(self.payload));self.html=self.row(self.digest)
        self.store=WorkbenchRelease(Path(self.tmp.name)/'app','0.2.29',runner=self.network)
    def row(self,digest):
        proof='' if digest is None else '<clipboard-copy aria-label="Copy to clipboard digest for '+self.name+'" value="'+digest+'"></clipboard-copy>'
        return '<li class="Box-row"><a href="'+self.url+'">installer</a>'+proof+'</li>'
    def network(self,request,*,timeout):
        url=request.full_url;self.calls.append((url,request.get_method()))
        if url==LATEST_URL:raise HTTPError(url,self.api_status,'synthetic limit',{},None)
        if url==self.latest:return Response(b'<html>Official latest</html>',self.final)
        if url==self.expanded:return Response(self.html.encode(),url)
        if url==self.url:
            if request.get_method()=='HEAD':
                response=Response(b'',url,length=self.length)
                def forbidden(*a,**k):raise AssertionError('Metadata HEAD must not download installer data')
                response.read=forbidden;return response
            return Response(self.payload,url)
        raise AssertionError('Unexpected network destination '+url)
    def test_403_and_429_use_same_repository_stable_latest_and_exact_head_length(self):
        for status in (403,429):
            self.api_status=status;self.calls=[]
            with self.subTest(status=status):
                result=self.store.check();self.assertTrue(result['available'])
                self.assertEqual(result['latest_version'],VERSION)
                self.assertEqual(result['asset']['size'],len(self.payload))
                self.assertEqual(result['asset']['digest'],self.digest)
                self.assertEqual(self.calls,[(LATEST_URL,'GET'),(self.latest,'GET'),(self.expanded,'GET'),(self.url,'HEAD')])
                self.assertFalse((self.store.data_dir/'updates').exists())
    def test_prepare_without_check_uses_fallback_and_verifies_actual_download(self):
        prepared=self.store.prepare()
        self.assertEqual(Path(prepared['installer']).read_bytes(),self.payload)
        self.assertEqual(prepared['sha256'],hashlib.sha256(self.payload).hexdigest())
    def test_unknown_latest_or_other_repo_redirects_never_establish_stability(self):
        for final in (self.latest,REPO_URL+'/releases/tag/v0.2.30-beta',REPO_URL+'/releases/tag/v00.2.30',
                      'https://github.com/another/repo/releases/tag/v0.2.30',self.final+'?unknown=1','https://example.org/release'):
            self.final=final
            with self.subTest(final=final),self.assertRaises(ValueError):self.store.check()
    def test_missing_or_ambiguous_digest_rows_are_rejected(self):
        for html in (self.row(None),self.row(self.digest)*2,self.row(self.digest).replace(self.name+'" value','WRONG.exe" value'),self.row('sha256:'+'0'*63)):
            self.html=html
            with self.subTest(html=html),self.assertRaises(ValueError):self.store.check()
    def test_inexact_or_unbounded_size_is_rejected(self):
        for length in ('0','-1','3.2','unknown',str(MAX_INSTALLER+1)):
            self.length=length
            with self.subTest(length=length),self.assertRaises(ValueError):self.store.check()
    def test_404_is_unpublished_and_other_failure_is_not_false_latest(self):
        self.api_status=404;result=self.store.check();self.assertEqual(result['reason'],'unpublished')
        self.assertEqual(self.calls,[(LATEST_URL,'GET')]);self.calls=[];self.api_status=503
        with self.assertRaises(HTTPError):self.store.check()
        self.assertEqual(self.calls,[(LATEST_URL,'GET')])

if __name__=='__main__':unittest.main()
