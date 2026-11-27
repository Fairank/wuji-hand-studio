import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import official_device_discovery as discovery


class DiscoveryTests(unittest.TestCase):
    def result(self,devices):return SimpleNamespace(returncode=0,stdout=json.dumps({'devices':devices}),stderr='')

    def run_scan(self,results):
        with patch('native_cli.prepare',return_value={'binary':'fake-official.exe'}),patch.object(discovery.subprocess,'run',side_effect=results) as run,patch.object(discovery.time,'sleep') as sleep:
            value=discovery.native_devices()
            return value,run,sleep

    def test_nonempty_discovery_not_retried(self):
        row={'sn':'WG_SYNTHETIC'}
        value,run,sleep=self.run_scan([self.result([row])])
        self.assertEqual(value['devices'],[row]);self.assertEqual(run.call_count,1);sleep.assert_not_called()

    def test_one_empty_interval_gets_one_bounded_retry(self):
        row={'sn':'WG_SYNTHETIC'}
        value,run,sleep=self.run_scan([self.result([]),self.result([row])])
        self.assertEqual(value['devices'],[row]);self.assertEqual(run.call_count,2);sleep.assert_called_once()

    def test_persistent_empty_stays_empty_after_two_scans(self):
        value,run,sleep=self.run_scan([self.result([]),self.result([])])
        self.assertEqual(value['devices'],[]);self.assertEqual(run.call_count,2)

    def test_cli_failure_not_retried_or_hidden(self):
        with patch('native_cli.prepare',return_value={'binary':'fake.exe'}),patch.object(discovery.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='Official failure')) as run:
            with self.assertRaisesRegex(ValueError,'Official failure'):discovery.native_devices()
            self.assertEqual(run.call_count,1)

    def test_structured_discovery_error_uses_diagnostic_without_accepting_rows(self):
        payload = dict(devices=[dict(sn='WG_SYNTHETIC')],
                       error=dict(code=4, kind='unavailable', reason='no_route',
                                  message='No usable IPv4 discovery interface', hint=None))
        with patch('native_cli.prepare',return_value={'binary':'fake.exe'}), \
                patch.object(discovery.subprocess,'run',return_value=SimpleNamespace(
                    returncode=4,stdout=json.dumps(payload),stderr='Unrelated diagnostic')) as run:
            with self.assertRaises(ValueError) as caught:
                discovery.native_devices()
        self.assertEqual(str(caught.exception), 'No usable IPv4 discovery interface')
        self.assertEqual(run.call_count, 1)

    def test_device_rows_with_error_cannot_be_published_as_fresh(self):
        payload = dict(devices=[dict(sn='WG_SYNTHETIC')], error=dict(code=4, reason='no_response'))
        with patch('native_cli.prepare',return_value={'binary':'fake.exe'}), \
                patch.object(discovery.subprocess,'run',return_value=SimpleNamespace(
                    returncode=0,stdout=json.dumps(payload),stderr='')):
            with self.assertRaises(ValueError):
                discovery.native_devices()

    def test_malformed_output_not_retried(self):
        with patch('native_cli.prepare',return_value={'binary':'fake.exe'}),patch.object(discovery.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{}',stderr='')) as run:
            with self.assertRaises(ValueError):discovery.native_devices()
            self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
