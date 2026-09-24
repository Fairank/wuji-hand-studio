from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from device_fleet import DeviceFleet


class FleetIsolationTests(unittest.TestCase):
    def test_slow_child_does_not_block_next_heartbeat_for_other_hand(self):
        with tempfile.TemporaryDirectory() as folder:
            fleet=DeviceFleet(folder,Path(folder),'http://127.0.0.1:9999')
            blocked=threading.Event();fast=threading.Event();calls=[]
            fleet.children={key:dict(id=key,process=SimpleNamespace(poll=lambda:None)) for key in ('slow','fast')}
            def post(record,command):
                self.assertEqual(command['name'],'session_keepalive')
                calls.append(record['id'])
                if record['id']=='slow':blocked.wait(2)
                else:fast.set()
            try:
                with patch.object(fleet,'post',side_effect=post):
                    before=time.monotonic();fleet.heartbeat()
                    self.assertLess(time.monotonic()-before,.5)
                    self.assertTrue(fast.wait(1))
                    # Wait until the fast Future completes without waiting for slow.
                    fleet._beat_pending['fast'].result(timeout=1);fast.clear()
                    fleet.heartbeat();self.assertTrue(fast.wait(1))
                    self.assertEqual(calls.count('slow'),1)
                    self.assertGreaterEqual(calls.count('fast'),2)
            finally:
                blocked.set();fleet._beat_pool.shutdown(wait=True)

    def test_rename_is_persistent_and_does_not_touch_session_or_profile(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            fleet=DeviceFleet(folder,folder,'http://127.0.0.1:9999')
            row=dict(id='a'*16,label='Left',profile='hand2_left',port=9998)
            fleet.records.write_text(json.dumps([row]))
            fleet.children[row['id']]=dict(row,process=object())
            try:
                fleet.rename(row['id'],'Left rig')
                saved=json.loads(fleet.records.read_text())
                self.assertEqual(saved,[dict(row,label='Left rig')])
                self.assertEqual(fleet.children[row['id']]['profile'],'hand2_left')
            finally:fleet._beat_pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
