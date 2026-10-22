"""Pinned public image-cache tests; synthetic bytes, no network or user cache."""
from contextlib import contextmanager
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from PIL import Image
import official_calibration_guides as guides
import runtime_paths


def image_bytes(format='PNG'):
    stream=io.BytesIO()
    Image.new('RGB',(4,3),(12,70,140)).save(stream,format=format)
    return stream.getvalue()


def item_for(data,mime='image/png'):
    return dict(size=len(data),sha256=hashlib.sha256(data).hexdigest(),width=4,height=3,
                mime=mime,url='https://mintcdn.com/synthetic/pinned-pose.png')


class Response:
    def __init__(self,data,mime,url='https://mintcdn.com/synthetic/pinned-pose.png'):
        self.data,self.url=data,url
        self.headers=Message();self.headers['Content-Type']=mime
        self.reads=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,size):self.reads.append(size);return self.data[:size]
    def geturl(self):return self.url


class GuideCacheTests(unittest.TestCase):
    @contextmanager
    def environment(self,data=None,item=None,response_mime=None):
        base=Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0231/qa/guide-test-temp'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            folder=Path(folder);resource=folder/'resource';resource.mkdir();cache=folder/'data'
            data=image_bytes() if data is None else data
            item=item_for(data) if item is None else item
            manifest=dict(poses=[dict(item) for _ in range(6)])
            (resource/'official_calibration_guides.json').write_text(json.dumps(manifest),encoding='utf-8')
            response=Response(data,response_mime or item['mime'])
            opener=Mock();opener.open.return_value=response
            with patch.object(runtime_paths,'RESOURCE',resource),patch.object(runtime_paths,'DATA',cache), \
                    patch.object(guides.urllib.request,'build_opener',return_value=opener) as build:
                yield dict(folder=folder,resource=resource,cache=cache,data=data,item=item,
                           opener=opener,build=build,response=response,manifest=manifest)

    def rewrite_manifest(self,env,manifest):
        (env['resource']/'official_calibration_guides.json').write_text(json.dumps(manifest),encoding='utf-8')

    def test_valid_download_is_exact_verified_atomic_and_requests_pinned_mime(self):
        with self.environment() as env:
            actual,mime=guides.read_guide(0)
            self.assertEqual(actual,env['data']);self.assertEqual(mime,'image/png')
            path=env['cache']/'official-calibration-guides'/(env['item']['sha256']+'.image')
            self.assertEqual(path.read_bytes(),env['data'])
            self.assertEqual(list(path.parent.glob('*.tmp')),[])
            request=env['opener'].open.call_args.args[0]
            self.assertEqual(request.full_url,env['item']['url'])
            self.assertEqual(request.get_header('Accept'),'image/png')
            self.assertEqual(env['opener'].open.call_args.kwargs['timeout'],8)
            self.assertEqual(env['response'].reads,[guides._MAX+1])

    def test_warm_cache_remains_available_offline_without_network(self):
        with self.environment() as env:
            expected=guides.read_guide(0)
            env['build'].reset_mock();env['build'].side_effect=AssertionError('Network is offline')
            self.assertEqual(guides.read_guide(0),expected)
            env['build'].assert_not_called()

    def test_cold_cache_network_failure_is_readable_and_never_fabricates_an_image(self):
        with self.environment() as env:
            env['opener'].open.side_effect=urllib.error.URLError('synthetic offline')
            with self.assertRaisesRegex(OSError,'Official pose reference is temporarily unavailable'):
                guides.read_guide(0)
            self.assertEqual(list(env['cache'].rglob('*.image')),[])
            self.assertEqual(list(env['cache'].rglob('*.tmp')),[])

    def test_hash_or_length_mismatch_never_creates_cache_entry(self):
        for mutation in ['sha','length']:
            with self.subTest(mutation=mutation), self.environment() as env:
                item=env['manifest']['poses'][0]
                if mutation=='sha':item['sha256']='0'*64
                else:item['size']+=1
                self.rewrite_manifest(env,env['manifest'])
                with self.assertRaisesRegex(ValueError,'integrity'):
                    guides.read_guide(0)
                self.assertEqual(list(env['cache'].rglob('*.image')),[])

    def test_corrupt_cached_image_is_not_served_or_hidden_by_network_fallback(self):
        with self.environment() as env:
            folder=env['cache']/'official-calibration-guides';folder.mkdir(parents=True)
            path=folder/(env['item']['sha256']+'.image');path.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'integrity'):guides.read_guide(0)
            env['build'].assert_not_called()

    def test_response_mime_must_match_pinned_metadata(self):
        with self.environment(response_mime='image/jpeg') as env:
            with self.assertRaisesRegex(ValueError,'content type'):guides.read_guide(0)
            self.assertEqual(list(env['cache'].rglob('*.image')),[])

    def test_actual_image_format_must_match_declared_mime_even_with_correct_hash(self):
        # The metadata and HTTP header cannot make PNG bytes into JPEG. The
        # browser must receive the MIME which matches the verified encoding.
        data=image_bytes('PNG');item=item_for(data,'image/jpeg')
        with self.environment(data,item,response_mime='image/jpeg'):
            with self.assertRaisesRegex(ValueError,'image|format|type'):
                guides.read_guide(0)

    def test_dimensions_and_invalid_image_bytes_are_not_accepted(self):
        for data,item in [(image_bytes(),{**item_for(image_bytes()),'width':5}),
                          (b'not an image',item_for(b'not an image'))]:
            with self.subTest(size=len(data)),self.environment(data,item):
                with self.assertRaises((ValueError,OSError)):guides.read_guide(0)

    def test_download_over_limit_is_bounded_and_never_cached(self):
        data=b'x'*(guides._MAX+10)
        with self.environment(data,item_for(b'x')) as env:
            with self.assertRaisesRegex(ValueError,'integrity|limit'):guides.read_guide(0)
            self.assertEqual(env['response'].reads,[guides._MAX+1])
            self.assertEqual(list(env['cache'].rglob('*.image')),[])

    def test_invalid_step_or_manifest_never_requests_network(self):
        with self.environment() as env:
            for value in [True,False,-1,6,1.0,'0',None]:
                with self.subTest(step=value),self.assertRaises(ValueError):guides.read_guide(value)
            for poses in [None,{},[],[env['item']]*5]:
                self.rewrite_manifest(env,dict(poses=poses))
                with self.subTest(poses=poses),self.assertRaises(ValueError):guides.read_guide(0)
            env['build'].assert_not_called()

    def test_metadata_type_size_mime_and_dimension_limits_precede_network(self):
        for change in [dict(size=True),dict(size=0),dict(size=guides._MAX+1),dict(mime='text/html'),
                       dict(width=True),dict(height=4097),dict(sha256='short')]:
            with self.subTest(change=change),self.environment() as env:
                env['manifest']['poses'][0].update(change);self.rewrite_manifest(env,env['manifest'])
                with self.assertRaises(ValueError):guides.read_guide(0)
                env['build'].assert_not_called()

    def test_only_https_exact_official_host_and_same_host_redirect_are_allowed(self):
        for url in ['http://mintcdn.com/x','https://mintcdn.com.evil.test/x','https://example.test/x',
                    'https://user@mintcdn.com/x','https://mintcdn.com:444/x','https://mintcdn.com/x#fragment']:
            with self.subTest(url=url),self.assertRaises(ValueError):guides._url(url)
        with self.environment() as env:
            env['response'].url='https://example.test/redirected-image.png'
            with self.assertRaisesRegex(ValueError,'source'):guides.read_guide(0)
            self.assertEqual(env['response'].reads,[])


if __name__=='__main__':unittest.main()
