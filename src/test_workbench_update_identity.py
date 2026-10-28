"""A fresh check identifies exact bytes; same tags do not authorize old packets.

Real service/store and isolated fixture files; network and installer are doubles.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path

import test_workbench_update_service as service_fixtures
from test_workbench_release import Network, release
from workbench_release import LATEST_URL, WorkbenchRelease


class PreparedIdentityTests(unittest.TestCase):
    def check(self,env):
        env.service.start('check');env.service.thread.run_to_end()
        return env.service.snapshot()

    def prepare(self,env):
        env.service.start('prepare');env.service.thread.run_to_end()
        return env.service.snapshot()

    def test_exact_current_packet_survives_recheck_without_downloading(self):
        with service_fixtures.ServiceTests().environment() as env:
            service_fixtures.ServiceTests().ready(env)
            state=self.check(env)
            self.assertEqual(state['phase'],'ready');self.assertTrue(state['can_apply'])
            self.assertEqual(state['prepared'],env.prepared)
            env.store.prepare.assert_not_called();env.popen.assert_not_called()

    def test_changed_or_unproven_asset_clears_apply_authority_but_preserves_cache(self):
        changes=[dict(digest='sha256:'+'f'*64),dict(size=len(service_fixtures.PAYLOAD)+1),
                 dict(browser_download_url='https://github.com/other/packet.exe'),
                 dict(name='other.exe'),dict(digest=None),dict(digest='sha256:bad'),
                 dict(size=True),dict(browser_download_url=None)]
        for change in changes:
            with self.subTest(change=change),service_fixtures.ServiceTests().environment() as env:
                service_fixtures.ServiceTests().ready(env)
                env.store.check.return_value['asset'].update(change)
                state=self.check(env)
                for _ in range(3):
                    self.assertEqual(state['phase'],'available');self.assertIsNone(state['prepared'])
                    self.assertFalse(state['can_apply']);state=env.service.snapshot()
                with self.assertRaisesRegex(ValueError,'Download and verify'):env.service.apply()
                self.assertEqual(env.source.read_bytes(),service_fixtures.PAYLOAD)
                env.store.prepare.assert_not_called();env.popen.assert_not_called()

    def test_stale_packet_is_rejected_through_actual_apply_http_bridge(self):
        with service_fixtures.ServiceTests().environment() as env:
            service_fixtures.ServiceTests().ready(env)
            env.store.check.return_value['asset']['digest']='sha256:'+'f'*64
            self.check(env)
            handler,_=service_fixtures.UpdateAPIBridgeTests().handler({'name':'workbench_update_apply'})
            handler.server.updates=env.service
            service_fixtures.handler_method('do_POST')(handler)
            status,body=handler.reply.call_args.args
            self.assertEqual(status,400);self.assertFalse(body['ok'])
            self.assertIn('Download and verify',body['error']);env.popen.assert_not_called()

    def test_recheck_busy_cannot_apply_before_new_identity_is_committed(self):
        with service_fixtures.ServiceTests().environment() as env:
            service_fixtures.ServiceTests().ready(env)
            env.store.check.return_value['asset']['digest']='sha256:'+'f'*64
            env.service.start('check')
            self.assertFalse(env.service.snapshot()['can_apply'])
            with self.assertRaisesRegex(ValueError,'Download and verify'):env.service.apply()
            env.service.thread.run_to_end()
            self.assertIsNone(env.service.snapshot()['prepared']);env.popen.assert_not_called()

    def test_unpublished_and_unavailable_results_retire_prepared_authority(self):
        for result in (dict(available=False,reason='unpublished'),dict(available=False,latest_version='0.2.29'),
                       dict(available=True,latest_version=service_fixtures.VERSION)):
            with self.subTest(result=result),service_fixtures.ServiceTests().environment() as env:
                service_fixtures.ServiceTests().ready(env);env.store.check.return_value=result
                state=self.check(env)
                self.assertIsNone(state['prepared']);self.assertFalse(state['can_apply'])
                self.assertTrue(env.source.is_file())
                with self.assertRaises(ValueError):env.service.apply()

    def test_a_later_failed_check_cannot_restore_invalidated_packet(self):
        with service_fixtures.ServiceTests().environment() as env:
            service_fixtures.ServiceTests().ready(env)
            env.store.check.return_value['asset']['digest']='sha256:'+'f'*64
            self.check(env);env.store.check.side_effect=OSError('Synthetic check failed')
            state=self.check(env)
            self.assertEqual(state['phase'],'error');self.assertIsNone(state['prepared'])
            self.assertFalse(state['can_apply'])
            with self.assertRaises(ValueError):env.service.apply()

    def real_store(self,env,payload):
        metadata=release(payload);network=Network()
        url=metadata['assets'][0]['browser_download_url']
        network.responses[LATEST_URL]=json.dumps(metadata).encode()
        network.responses[url]=payload
        env.service.store=WorkbenchRelease(env.data,service_fixtures.EDITION['version'],runner=network)
        return metadata,network,url

    def test_same_tag_replacement_is_explicitly_downloaded_verified_and_usable(self):
        with service_fixtures.ServiceTests().environment() as env:
            old=b'MZ old isolated packet';new=b'MZ replacement isolated packet'
            metadata,network,url=self.real_store(env,old)
            self.check(env);before=self.prepare(env)
            self.assertEqual(before['phase'],'ready')
            old_source=Path(before['prepared']['installer'])
            metadata=release(new);network.responses[LATEST_URL]=json.dumps(metadata).encode()
            network.responses[url]=new
            state=self.check(env)
            self.assertEqual(state['phase'],'available');self.assertFalse(state['can_apply'])
            self.assertEqual(old_source.read_bytes(),old)
            self.assertEqual(sum(call[0]==url for call in network.calls),1)
            ready=self.prepare(env)
            self.assertEqual(ready['phase'],'ready');self.assertTrue(ready['can_apply'])
            self.assertEqual(ready['prepared']['sha256'],hashlib.sha256(new).hexdigest())
            self.assertEqual(old_source.read_bytes(),new)
            self.assertEqual(sum(call[0]==url for call in network.calls),2)
            # Recheck does not download again and the retained old packet is history.
            self.assertEqual(self.check(env)['phase'],'ready')
            self.assertEqual(sum(call[0]==url for call in network.calls),2)
            self.assertEqual([p.read_bytes() for p in (env.data/'updates/retired').rglob('*.exe')],[old])
            env.popen.assert_not_called()

    def test_replacement_download_failure_never_falls_back_and_retry_succeeds(self):
        with service_fixtures.ServiceTests().environment() as env:
            old=b'MZ old isolated packet';new=b'MZ replacement isolated packet'
            metadata,network,url=self.real_store(env,old)
            self.check(env);self.prepare(env)
            network.responses[LATEST_URL]=json.dumps(release(new)).encode()
            network.responses[url]=OSError('Synthetic transfer failure')
            self.check(env);failed=self.prepare(env)
            self.assertEqual(failed['phase'],'error');self.assertIsNone(failed['prepared'])
            self.assertFalse(failed['can_apply'])
            with self.assertRaises(ValueError):env.service.apply()
            network.responses[url]=new;ready=self.prepare(env)
            self.assertEqual(ready['phase'],'ready');self.assertTrue(ready['can_apply'])
            self.assertEqual(Path(ready['prepared']['installer']).read_bytes(),new)
            self.assertEqual(sum(call[0]==url for call in network.calls),3)
            env.popen.assert_not_called()

    def test_sidecar_only_recheck_requires_reverification_without_redownloading_valid_bytes(self):
        with service_fixtures.ServiceTests().environment() as env:
            payload=b'MZ sidecar isolated packet';metadata,network,url=self.real_store(env,payload)
            asset=metadata['assets'][0];asset.pop('digest')
            checksum=(hashlib.sha256(payload).hexdigest()+'  '+asset['name']+'\n').encode()
            checksum_url=url+'.sha256'
            metadata['assets'].append(dict(name=asset['name']+'.sha256',browser_download_url=checksum_url,size=len(checksum)))
            network.responses[LATEST_URL]=json.dumps(metadata).encode();network.responses[checksum_url]=checksum
            self.check(env);self.assertTrue(self.prepare(env)['can_apply'])
            stale=self.check(env)
            self.assertEqual(stale['phase'],'available');self.assertIsNone(stale['prepared'])
            self.assertFalse(stale['can_apply'])
            ready=self.prepare(env)
            self.assertEqual(ready['phase'],'ready');self.assertTrue(ready['can_apply'])
            self.assertEqual(sum(call[0]==url for call in network.calls),1)
            self.assertEqual(sum(call[0]==checksum_url for call in network.calls),2)
            env.popen.assert_not_called()


if __name__=='__main__':unittest.main()
