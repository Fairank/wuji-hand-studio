"""The frame envelope identifies the same already-public local service as state."""
import base64
import copy
import io
import json
from types import SimpleNamespace as N
import unittest

from console_server import Handler


class ViewerServiceEnvelopeTests(unittest.TestCase):
    def test_all_display_selectors_bind_the_serving_controller_without_mutating_frame_metadata(self):
        original=dict(ready=True,mode='glove_preview',frame_age_ms=20,frame_timeout_ms=250,
                      target_frame=dict(source='official_sdk',source_seq=1))
        calls=[]
        def get(**kwargs):calls.append(kwargs);return b'synthetic-jpeg-no-real-renderer',original
        for selector in ('','?teleoperation=preview','?profile=hand1_right','?profile=hand2_left&display=preview'):
            with self.subTest(selector=selector):
                output,status=io.BytesIO(),{}
                controller=N(csrf='synthetic-local-service-A')
                target=N(path='/api/view'+selector,server=N(controller=controller,pose=N(get=get)),
                         allowed_host=lambda:True,send_response=lambda v:status.update(code=v),
                         send_header=lambda *a:None,end_headers=lambda:None,wfile=output)
                target.reply=lambda *a,**k:Handler.reply(target,*a,**k)
                Handler.do_GET(target)
                result=json.loads(output.getvalue());self.assertEqual(status['code'],200)
                self.assertEqual(result.get('service_token'),controller.csrf)
                self.assertEqual(result['meta'],original);self.assertNotIn('service_token',original)
                self.assertEqual(base64.b64decode(result['image'].split(',')[1]),b'synthetic-jpeg-no-real-renderer')
        self.assertEqual(calls,[{},dict(teleoperation='preview'),dict(profile='hand1_right'),dict(profile='hand2_left',display='preview')])

    def test_replacement_services_with_identical_frame_identity_have_distinct_envelopes(self):
        results=[]
        for token in ('old-service-A','new-service-B'):
            out=[]
            target=N(path='/api/view',server=N(controller=N(csrf=token),pose=N(get=lambda:(None,dict(ready=False)))),allowed_host=lambda:True)
            target.reply=lambda status,body,**kw:out.append((status,copy.deepcopy(body)))
            Handler.do_GET(target);self.assertEqual(out[0][0],200);results.append(out[0][1])
        self.assertNotEqual(results[0].get('service_token'),results[1].get('service_token'))
        self.assertEqual(results[0]['meta'],results[1]['meta'])


if __name__=='__main__':unittest.main()
