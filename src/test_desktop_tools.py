import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from desktop_tools import dispatch, verify_update, idle


class DesktopToolsTests(unittest.TestCase):
    def test_reject_arbitrary_script_shell_and_pages(self):
        host=Mock()
        for payload in ({'operation':'exec','script':'anything'}, {'operation':'page','page':'http://example.com'},
                        {'operation':'resize','width':True,'height':700},
                        {'operation':'appearance','key':'reduceMotion','value':'false'},None):
            with self.assertRaises(ValueError):dispatch(host,payload)
        self.assertEqual(host.mock_calls,[])

    def test_busy_never_closes_or_updates(self):
        host=Mock();host.server.controller.snapshot.return_value=dict(connection='connected')
        host.server.controller.doctor.snapshot.return_value={}
        for op in ('close_idle','prepare_update','reload_ui'):
            with self.assertRaises(ValueError):dispatch(host,dict(operation=op))
        host.stop_and_close.assert_not_called()
        host.reload_ui.assert_not_called()

    def test_connection_popover_is_view_only_and_constrained(self):
        host=Mock()
        dispatch(host,dict(operation='connection_panel',state='open'))
        host.connection_panel.assert_called_once_with('open')
        with self.assertRaises(ValueError):dispatch(host,dict(operation='connection_panel',state='execute'))
        host.server.controller.action.assert_not_called()

    def test_official_tools_page_only_navigates(self):
        host=Mock();dispatch(host,dict(operation='page',page='control-tools'))
        host.navigate.assert_called_once_with('control-tools');host.server.controller.action.assert_not_called()
        with self.assertRaises(ValueError):dispatch(host,dict(operation='page',page='official-studio'))

    def test_playlist_picker_only_opens_editor(self):
        host=Mock()
        dispatch(host,dict(operation='picker',kind='playlist'))
        host.picker.assert_called_once_with('playlist')
        for kind in ('play','hardware','program_start'):
            with self.assertRaises(ValueError):dispatch(host,dict(operation='picker',kind=kind))
        host.server.controller.action.assert_not_called()

    def test_idle_rejects_unknown_hardware_or_unfinished_work(self):
        state=dict(connection='disconnected',hardware=dict(active=False))
        self.assertTrue(idle(state,{}))
        for addition in (dict(hardware={}),dict(glove=dict(busy=True)),dict(recording=dict(active=True)),dict(parameter_sync=dict(busy=True))):
            self.assertFalse(idle(dict(state,**addition),{}))
        self.assertFalse(idle(state,dict(running=True)))

    def test_released_viewer_is_not_idle_while_a_handover_is_starting(self):
        # During explicit handover the Viewer may already be gone while the
        # destination has not published its new owner. Code tools must not close
        # or reload the application in this legitimate transition gap.
        for key in ('calibration_connection','teleoperation_connection'):
            host=Mock()
            host.server.controller.snapshot.return_value=dict(
                connection='disconnected',hardware=dict(active=False),
                official_viz=dict(active=False,busy=False,reserved=False),
                **{key:dict(busy=True,viewer_released=True,phase='starting')})
            host.server.controller.doctor.snapshot.return_value={}
            for operation in ('close_idle','prepare_update','reload_ui','preview'):
                payload=dict(operation=operation)
                if operation=='preview':payload.update(action='open',speed=1)
                with self.assertRaises(ValueError):dispatch(host,payload)
            host.stop_and_close.assert_not_called();host.reload_ui.assert_not_called()
            host.preview.assert_not_called()
            host.server.controller.snapshot.return_value[key]['busy']=False
            dispatch(host,dict(operation='preview',action='open',speed=1))
            host.preview.assert_called_once_with('open',1)

    def test_ended_calibration_with_unconfirmed_cleanup_cannot_close_reload_or_update(self):
        state=dict(connection='disconnected',hardware=dict(active=False),
                   calibration=dict(running=False,cleanup_required=True))
        self.assertFalse(idle(state,{}))
        host=Mock();host.server.controller.snapshot.return_value=state
        host.server.controller.doctor.snapshot.return_value={}
        for operation in ('close_idle','prepare_update','reload_ui','preview'):
            payload=dict(operation=operation)
            if operation=='preview':payload.update(action='open',speed=1)
            with self.subTest(operation=operation),self.assertRaises(ValueError):dispatch(host,payload)
        host.stop_and_close.assert_not_called();host.reload_ui.assert_not_called();host.preview.assert_not_called()
        state['calibration']['cleanup_required']=False
        self.assertTrue(idle(state,{}))
        for nested in (False,True):
            run=dict(id='a'*32,source='official_jsonl_native',running=False,cleanup_confirmed=False)
            state['calibration']={'run':run} if nested else run
            with self.subTest(nested=nested):
                self.assertFalse(idle(state,{}))
                run['cleanup_confirmed']=True
                self.assertTrue(idle(state,{}))

    def test_update_requires_edition_hash_version_and_local_path(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'updates').mkdir();data=b'MZ'+b'test installer';digest=hashlib.sha256(data).hexdigest()
            p=root/'updates/HandWorkbench-0.1.5-windows-x64-setup.exe';p.write_bytes(data)
            edition=dict(name='basic',version='0.1.4')
            self.assertEqual(verify_update(root,edition,'0.1.5',digest)['installer'],str(p))
            for version,sha,ed in [('0.1.5','0'*64,edition),('0.1.3',digest,edition),('../0.1.5',digest,edition),('0.1.5',digest,dict(name='workbench',version='0.1.6'))]:
                with self.assertRaises(ValueError):verify_update(root,ed,version,sha)
            p.write_bytes(b'not an exe')
            with self.assertRaises(ValueError):verify_update(root,edition,'0.1.5',hashlib.sha256(p.read_bytes()).hexdigest())


if __name__=='__main__':unittest.main()
