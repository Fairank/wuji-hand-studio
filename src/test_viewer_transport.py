import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qsl,urlsplit,urlencode
from viewer_transport import ViewerTransport,BLOCKED
from official_visualization import OfficialVisualization


def url(port=9876,outer=9090):return 'http://127.0.0.1:'+str(outer)+'/?'+urlencode({'url':'rerun+http://127.0.0.1:'+str(port)+'/proxy','theme':'dark','persist':'1'})


class ViewerTransportTests(unittest.TestCase):
    def test_safe_ports_keep_url_without_allocating_socket(self):
        factory=Mock();transport=ViewerTransport(url(),factory)
        self.assertEqual(transport.open(),url());factory.assert_not_called();self.assertFalse(transport.forwarded)

    def test_unsafe_data_port_rewritten_preserving_exact_proxy_and_theme(self):
        relay=SimpleNamespace(port=51000,close=Mock(return_value=True));factory=Mock(return_value=relay)
        transport=ViewerTransport(url(5061),factory);changed=transport.open()
        factory.assert_called_once_with(5061)
        query=dict(parse_qsl(urlsplit(changed).query))
        self.assertEqual(query,{'url':'rerun+http://127.0.0.1:51000/proxy','theme':'dark','persist':'1'})
        self.assertEqual(urlsplit(changed).port,9090);self.assertTrue(transport.close());relay.close.assert_called_once()

    def test_both_unsafe_endpoints_get_separate_relays(self):
        factory=Mock(side_effect=[SimpleNamespace(port=51000,close=Mock(return_value=True)),SimpleNamespace(port=51001,close=Mock(return_value=True))])
        transport=ViewerTransport(url(5061,5060),factory);changed=transport.open()
        self.assertEqual([c.args[0] for c in factory.call_args_list],[5061,5060]);self.assertEqual(urlsplit(changed).port,51001);self.assertTrue(transport.close())

    def test_external_credentials_and_invalid_stream_rejected_before_factory(self):
        for bad in ('http://example.com:5060/?'+urlencode({'url':'rerun+http://127.0.0.1:5061/proxy'}),url().replace('127.0.0.1%3A9876','example.com%3A9876'),url().replace('%2Fproxy','%2Fcontrol')):
            with self.subTest(bad=bad):
                factory=Mock()
                with self.assertRaises(ValueError):ViewerTransport(bad,factory)
                factory.assert_not_called()

    def test_partial_failure_closes_created_relay_and_never_publishes_url(self):
        relay=SimpleNamespace(port=5061,close=Mock(return_value=True));t=ViewerTransport(url(5061),Mock(return_value=relay))
        with self.assertRaises(ValueError):t.open()
        self.assertEqual(relay.close.call_count,8);self.assertIsNone(t.url)

    def test_os_assigning_a_blocked_temporary_port_gets_bounded_retry(self):
        bad=SimpleNamespace(port=6000,close=Mock(return_value=True));good=SimpleNamespace(port=51000,close=Mock(return_value=True))
        factory=Mock(side_effect=[bad,good]);t=ViewerTransport(url(5061),factory)
        self.assertIn('51000',t.open());bad.close.assert_called_once();self.assertEqual(t.relays,[good]);self.assertTrue(t.close())

    def test_partial_close_failure_retains_holder_and_prevents_reopen(self):
        relay=SimpleNamespace(port=5061,close=Mock(return_value=False));t=ViewerTransport(url(5061),Mock(return_value=relay))
        with self.assertRaises(ValueError):t.open()
        self.assertEqual(t.relays,[relay]);self.assertFalse(t.close())
        with self.assertRaisesRegex(ValueError,'unconfirmed'):t.open()
        relay.close.return_value=True;self.assertTrue(t.close())

    def test_manager_cleanup_failure_retains_transport(self):
        value=OfficialVisualization();holder=SimpleNamespace(close=Mock(side_effect=OSError('synthetic cleanup')))
        value.transport=holder
        self.assertFalse(value._close_transport());self.assertIs(value.transport,holder)
        holder.close=Mock(return_value=True);self.assertTrue(value._close_transport());self.assertIsNone(value.transport)

    def test_known_browser_unsafe_ports_are_included(self):
        for port in (5060,5061,6000,6667,10080):self.assertIn(port,BLOCKED)
        for port in (8783,9090,9876,51000):self.assertNotIn(port,BLOCKED)


if __name__=='__main__':unittest.main()
