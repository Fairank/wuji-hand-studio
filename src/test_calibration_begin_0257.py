"""Explicit calibration entry: actual coordinator, isolated owners and devices."""
from contextlib import contextmanager
import copy
import threading
import unittest
from unittest.mock import Mock,patch

import cli_coordination
import test_calibration_handover as fixture
SN=fixture.SN;TOKEN=fixture.TOKEN


class BeginTests(unittest.TestCase):
    @contextmanager
    def environment(self,rows=None):
        with fixture.HandoverTests().environment() as e:
            (e.folder/'session.json').unlink()
            e.viewer.token=None;e.viewer.process=None;e.viewer.native_job=None;e.viewer.transport=None
            e.viewer.state.update(active=False,busy=False,phase='idle',reported_device=None)
            e.controller.calibration_devices.return_value=(rows if rows is not None else
                [{'sn':SN,'model':'Wuji Glove 1','handedness':'left'}])
            yield e

    def command(self,**changes):
        return dict(name='calibration_begin',serial=SN,interface='guided',side='left',
                    profile='Demo',replace=False,create_profile=False,**changes)

    def test_direct_start_requires_no_viewer_or_duplicate_user_action(self):
        with self.environment() as e:
            result=e.service.begin(self.command())
            e.controller.calibration_devices.assert_called_once_with(fresh=True)
            self.assertEqual(e.calibration.start.call_args.args,('left',SN,False))
            receipt=e.calibration.start.call_args.kwargs['connected']
            self.assertEqual(receipt['source'],'explicit_calibration')
            self.assertEqual(receipt['serial'],SN);self.assertEqual(receipt['side'],'left')
            self.assertNotIn('viewer_token',receipt)
            self.assertEqual(result['calibration']['run']['status'],'starting')
            self.assertFalse(result['calibration_connection']['busy'])
            e.job.terminate_and_wait.assert_not_called();e.recover.assert_not_called()

    def test_same_viewer_uses_original_exact_owned_release(self):
        with fixture.HandoverTests().environment() as e:
            result=e.service.begin(self.command())
            self.assertEqual(e.calibration.start.call_args.kwargs['connected']['viewer_token'],TOKEN)
            e.job.terminate_and_wait.assert_called_once()
            e.controller.calibration_devices.assert_not_called()
            self.assertEqual(result['calibration']['run']['serial'],SN)

    def test_other_viewer_is_preserved(self):
        with fixture.HandoverTests().environment() as e:
            command=self.command();command['serial']='WG_OTHER'
            with self.assertRaisesRegex(ValueError,'Another device'):e.service.begin(command)
            e.job.terminate_and_wait.assert_not_called();e.calibration.start.assert_not_called()
            e.controller.calibration_devices.assert_not_called()

    def test_wrong_missing_ambiguous_or_non_glove_never_starts(self):
        for rows in ([],[{'sn':'WG_OTHER','model':'Wuji Glove'}],
                     [{'sn':SN,'model':'Wuji Hand 2'}],
                     [{'sn':SN,'model':'Wuji Glove'}]*2,
                     [{'sn':SN,'model':'Wuji Glove','side':'right'}]):
            with self.subTest(rows=rows),self.environment(rows) as e:
                with self.assertRaises(ValueError):e.service.begin(self.command())
                e.calibration.start.assert_not_called()
                self.assertFalse(e.service.snapshot()['busy'])

    def test_known_side_can_be_reused_and_unknown_requires_choice(self):
        with self.environment() as e:
            command=self.command();command['side']=None
            e.service.begin(command)
            self.assertEqual(e.calibration.start.call_args.args[0],'left')
        with self.environment([{'sn':SN,'model':'Wuji Glove'}]) as e:
            command=self.command();command['side']=None
            with self.assertRaisesRegex(ValueError,'Confirm'):e.service.begin(command)
            e.calibration.start.assert_not_called()

    def test_discovery_failure_and_retry_preserve_user_intent(self):
        with self.environment() as e:
            e.controller.calibration_devices.side_effect=OSError('synthetic unplug')
            with self.assertRaisesRegex(OSError,'unplug'):e.service.begin(self.command())
            e.calibration.start.assert_not_called()
            e.controller.calibration_devices.side_effect=None
            e.service.begin(self.command())
            self.assertEqual(e.calibration.start.call_count,1)

    def test_browser_cannot_supply_private_owner_receipt_or_typo_flags(self):
        for changes in ({'connected':{}},{'viewer_token':TOKEN},{'replace':1},
                        {'create_profile':1},{'interface':'unknown'},{'side':'LEFT'}):
            with self.subTest(changes=changes),self.environment() as e:
                command={**self.command(),**changes}
                with self.assertRaises(ValueError):e.service.begin(command)
                e.controller.calibration_devices.assert_not_called();e.calibration.start.assert_not_called()

    def test_unconfirmed_foreign_marker_blocks_before_discovery(self):
        with self.environment() as e:
            cli_coordination.atomic(e.folder/'session.json',dict(kind='firmware',pid=999,start='unknown'))
            with self.assertRaises(ValueError):e.service.begin(self.command())
            e.controller.calibration_devices.assert_not_called();e.calibration.start.assert_not_called()

    def test_cancellation_after_discovery_never_starts(self):
        with self.environment() as e:
            rows=copy.deepcopy(e.controller.calibration_devices.return_value)
            def discover(**kw):
                thread=threading.Thread(target=e.service.request_cancel);thread.start();thread.join(1)
                return rows
            e.controller.calibration_devices.side_effect=discover
            with self.assertRaisesRegex(ValueError,'cancelled'):e.service.begin(self.command())
            e.calibration.start.assert_not_called();self.assertFalse(e.service.state['busy'])

    def test_cancel_racing_publication_cancels_exact_new_run(self):
        with self.environment() as e:
            start=e.calibration.start.side_effect
            def publish(*args,**kw):
                result=start(*args,**kw)
                thread=threading.Thread(target=e.service.request_cancel);thread.start();thread.join(1)
                return result
            e.calibration.start.side_effect=publish
            result=e.service.begin(self.command())
            e.calibration.cancel.assert_called_once()
            self.assertEqual(result['calibration']['run']['status'],'cancelling')

    def test_failed_preflight_can_be_corrected_without_new_connection(self):
        with self.environment() as e:
            original=e.calibration.start.side_effect
            e.calibration.start.side_effect=ValueError('synthetic named user missing')
            with self.assertRaisesRegex(ValueError,'named user'):e.service.begin(self.command())
            e.calibration.start.side_effect=original
            e.service.begin(self.command())
            self.assertEqual(e.calibration.start.call_count,2)
            e.job.terminate_and_wait.assert_not_called()


if __name__=='__main__':unittest.main()
