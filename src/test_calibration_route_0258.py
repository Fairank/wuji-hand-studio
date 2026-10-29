"""Exact official route transport and display selectors; no device/renderer I/O."""
import copy
import threading
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

from calibration_transport import validate_glove_route,route_from_discovery
from console_server import Controller,Handler
from glove_discovery import connect_glove
from pose_view import PoseView,VirtualPoseSource

SN='WG_SYNTHETIC_ROUTE'
def route(address='192.0.2.15:50001',**kw):return dict(source='official_discovery',serial=SN,address=address,**kw)


class RouteTests(unittest.TestCase):
    def test_only_exact_routable_numeric_endpoint_and_serial_are_accepted(self):
        self.assertEqual(validate_glove_route(route('udp/192.0.2.15:50001'),SN),route())
        for address in ('localhost:50001','127.0.0.1:50001','0.0.0.0:50001','224.0.0.1:50001',
                        '169.254.1.1:1','255.255.255.255:1','192.0.2.1:0','192.0.2.1:65536',
                        '192.0.2.1:01','192.0.2.1:1/x','tcp/192.0.2.1:1','192.0.2.1:1\n'):
            with self.subTest(address=address),self.assertRaises(ValueError):validate_glove_route(route(address),SN)
        for bad in (dict(route(),serial='WG_OTHER'),dict(route(),source='browser'),dict(route(),extra=True)):
            with self.assertRaises(ValueError):validate_glove_route(bad,SN)

    def test_discovery_preserves_exact_identity_and_never_uses_another_address(self):
        rows=[dict(sn='WG_OTHER',device_type='wuji_glove',address='192.0.2.99:50001'),
              dict(sn=SN,device_type='wuji_glove',address='192.0.2.15:50001')]
        self.assertEqual(route_from_discovery(rows,SN),route())
        self.assertIsNone(route_from_discovery(rows,'WG_MISSING'))
        self.assertIsNone(route_from_discovery([dict(sn=SN,device_type='wuji_glove',address='zenoh://'+SN)],SN))
        for bad in (rows+[rows[1]],[dict(sn=SN,device_type='wuji_hand_2',address='192.0.2.15:50001')]):
            with self.assertRaises(ValueError):route_from_discovery(bad,SN)

    def test_direct_official_address_checks_readback_before_return_and_never_falls_back(self):
        for actual in (SN,'WG_OTHER'):
            with self.subTest(actual=actual):
                glove=N(serial_number=actual,disconnect=Mock());manager=N(connect=Mock(return_value=glove))
                selected=dict(serial=SN,address=route()['address'],source='official_address')
                if actual==SN:self.assertIs(connect_glove(manager,selected),glove)
                else:
                    with self.assertRaisesRegex(ValueError,'Glove identity changed'):connect_glove(manager,selected)
                    glove.disconnect.assert_called_once()
                manager.connect.assert_called_once_with(address=route()['address'],device_name='studio_glove')

    def test_controller_uses_fresh_official_directory_only_after_explicit_continue(self):
        controller=N(connection_switch=N(_config=Mock(return_value='config')),
            calibration_devices=Mock(return_value=[dict(sn=SN,device_type='wuji_glove',address=route()['address'])]),
            glove=N(start_calibrated=Mock(return_value=42)),_continuation_model_ready=Mock(),
            _continuation_profile='hand2_right',sdk_session_configs={})
        bundle={'serial':SN}
        with patch('bridge_config.load_config',return_value={}),patch('official_device_discovery.use_native',return_value=True):
            self.assertEqual(Controller._start_calibrated_session(controller,bundle),42)
        controller.calibration_devices.assert_called_once_with(fresh=True)
        self.assertEqual(controller.glove.start_calibrated.call_args.args[0]['glove_route'],route())
        self.assertEqual(bundle,{'serial':SN})


class VirtualDisplayTests(unittest.TestCase):
    def test_virtual_snapshot_cannot_change_device_state_or_hide_active_hardware(self):
        state=dict(view_source='feedback',teleoperation_view_mode='feedback',latest={'seq':99},
            glove=dict(connection='receiving',busy=True,hardware={'active':True},feedback={'device_id':'WH_SYNTH'},stream={'q':[.1]*20}))
        original=copy.deepcopy(state);controller=N(snapshot=lambda:copy.deepcopy(state),action=Mock())
        view=VirtualPoseSource(controller).snapshot()
        self.assertEqual(view['view_source'],'glove');self.assertEqual(view['teleoperation_view_mode'],'target')
        self.assertFalse(view['virtual_preview_idle']);self.assertTrue(view['glove']['hardware']['active'])
        self.assertEqual(state,original);controller.action.assert_not_called()

    def test_empty_virtual_view_is_static_but_receiving_stale_does_not_become_idle(self):
        controller=N(snapshot=Mock(return_value={'glove':{'connection':'disconnected','busy':False}}))
        self.assertTrue(VirtualPoseSource(controller).snapshot()['virtual_preview_idle'])
        controller.snapshot.return_value={'glove':{'connection':'receiving','busy':True,'stream':{'fresh':False}}}
        self.assertFalse(VirtualPoseSource(controller).snapshot()['virtual_preview_idle'])

    def test_selector_has_separate_cached_renderer_and_ordinary_view_is_unchanged(self):
        with patch('pose_view.threading.Thread') as thread:
            view=PoseView(N(snapshot=Mock()))
            self.assertIsNone(view.virtual_view)
            view.get(teleoperation='preview');child=view.virtual_view
            view.get(teleoperation='preview');self.assertIs(view.virtual_view,child)
            self.assertIsInstance(child.controller,VirtualPoseSource)
            self.assertEqual(thread.call_count,2)
            with self.assertRaises(ValueError):view.get(teleoperation='real')

    def test_http_get_selector_is_bounded_and_has_no_device_action(self):
        handler=object.__new__(Handler);handler.allowed_host=Mock(return_value=True);handler.reply=Mock()
        pose=N(get=Mock(return_value=(None,{'ready':False})));controller=N(action=Mock())
        handler.server=N(pose=pose,controller=controller)
        handler.path='/api/view?teleoperation=preview';handler.do_GET()
        pose.get.assert_called_once_with(teleoperation='preview');controller.action.assert_not_called()
        for path in ('/api/view?teleoperation=real','/api/view?teleoperation=preview&teleoperation=preview','/api/view?other=1'):
            pose.get.reset_mock();handler.reply.reset_mock();handler.path=path;handler.do_GET()
            self.assertEqual(handler.reply.call_args.args[0],400);pose.get.assert_not_called()


if __name__=='__main__':unittest.main()
