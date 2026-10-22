"""Production server gates + metadata snapshot; every CLI/process/device fake."""
from contextlib import contextmanager, ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock, patch

import calibration_cli
import cli_coordination as coordination
from console_server import ConsoleHTTPServer
import native_cli
from official_visualization import OfficialVisualization

OUT=Path(__file__).resolve().parents[3]/'outputs/cli-workspace-0232/qa'
SN='WG_SYNTHETIC_SCAN_METADATA'
TOKEN='d'*32
COMPONENT=dict(version='2026.10.12',binary='C:/synthetic/wuji.exe')


def wearer(name='Alex'):
    return dict(name=name,is_default=name=='Default',left_hand=dict(calibrated=False),right_hand=dict(calibrated=False))


@contextmanager
def scan_metadata_environment():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUT) as folder,ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,WUJI_OFFICIAL_CLI_DATA=folder))
        stack.enter_context(patch.object(coordination,'identity',side_effect=lambda pid:'synthetic-live' if pid==os.getpid() else None))
        stack.enter_context(patch('managed_runtime.status',return_value=dict(busy=False)))
        stack.enter_context(patch('bridge_config.load_config',return_value=dict(mode='native_cli')))
        stack.enter_context(patch.object(calibration_cli,'load_config',return_value=dict(mode='native_cli')))
        stack.enter_context(patch('official_device_discovery.use_native',return_value=True))
        prepare=stack.enter_context(patch.object(native_cli,'prepare',return_value=COMPONENT))
        commands=[]
        def local_users_only(*args):
            commands.append(args)
            if args==('user','list'):
                return dict(users=[dict(name='Default',is_default=True,calibrated=dict(left=False,right=False)),
                                   dict(name='Alex',is_default=False)])
            if args==('user','show'):return wearer('Default')
            if args==('user','show','Alex'):return wearer()
            raise AssertionError('Unexpected non-local-user CLI operation: '+repr(args))
        query=stack.enter_context(patch.object(native_cli,'json_command',side_effect=local_users_only))
        spawn=stack.enter_context(patch('windows_process_job.OwnedProcess',side_effect=AssertionError('No actual CLI process may start')))
        provider=Mock(return_value=[])
        calibration=calibration_cli.CalibrationCLI(device_provider=provider)
        viewer=OfficialVisualization(folder)
        viewer.token=TOKEN
        viewer.state.update(active=False,busy=True,scanning=True,phase='idle',device=None,
                            reported_device=None,devices=[],scanned_at=None)
        viewer.connection_record=Mock(side_effect=ValueError('Select the glove connected in this workspace'))
        marker=dict(kind='scan',token=TOKEN,pid=os.getpid(),start='synthetic-live')
        coordination.atomic(Path(folder)/'session.json',marker)
        base=dict(connection='disconnected',hardware=dict(active=False),glove=dict(busy=False),
                  calibration=dict(running=False),recording=dict(active=False),parameter_sync=dict(busy=False),
                  program=dict(active=False),group=dict(active=False),unconfirmed_devices=[])
        handover=N(internal=False,viewer_token=None,state=dict(busy=False,phase='idle'))
        controller=N(lock=threading.RLock(),official_viz=viewer,calibration=calibration,calibration_handover=handover,
            official_cli=N(startup=Mock()),official_tools=N(snapshot=Mock(return_value={'check':{'busy':False}})),
            doctor=N(snapshot=Mock(return_value={})),calibration_devices=provider)
        controller.snapshot=lambda:{**copy.deepcopy(base), 'official_viz':viewer.snapshot(),
                                    'calibration_connection':copy.deepcopy(handover.state)}
        server=object.__new__(ConsoleHTTPServer);server.controller=controller
        server.configure_official_cli(startup=False)
        yield N(folder=Path(folder),server=server,controller=controller,calibration=calibration,viewer=viewer,
                provider=provider,commands=commands,query=query,spawn=spawn,prepare=prepare,marker=marker)


class ScanMetadataContractTests(unittest.TestCase):
    def test_running_discovery_reads_local_wearers_without_a_viewer_or_device_command(self):
        with scan_metadata_environment() as env:
            state=env.calibration.snapshot(refresh=True)
            self.assertTrue(state['available'],state.get('error'))
            self.assertTrue(state['metadata_current'],state.get('metadata_error'))
            self.assertTrue(state['guided_native'])
            self.assertEqual(state['current']['name'],'Default')
            self.assertEqual([u['name'] for u in state['users']],['Default','Alex'])
            self.assertIs(state['users'][1]['right_hand']['calibrated'],False)
            self.assertEqual(env.commands,[('user','list'),('user','show'),('user','show','Alex')])
            env.provider.assert_called_once_with(False)
            env.viewer.connection_record.assert_not_called();env.spawn.assert_not_called()
            self.assertTrue(env.viewer.state['scanning']);self.assertFalse(env.viewer.state['active'])
            self.assertEqual(coordination.reservation(env.folder),env.marker)

    def test_metadata_read_does_not_authorize_profile_writes_or_calibration_during_scan(self):
        with scan_metadata_environment() as env:
            state=env.calibration.snapshot(refresh=True)
            self.assertTrue(state['available'],state.get('error'))
            reads=list(env.commands)
            for operation,name in [('create','NewWearer'),('switch','Alex')]:
                with self.subTest(operation=operation),self.assertRaises(ValueError):env.calibration.profile(operation,name)
            with self.assertRaises(ValueError):env.calibration.start('right',SN,profile='Alex')
            connected=dict(source='native_viewer',serial=SN,side='right',owner_pid=os.getpid(),
                           owner_start='synthetic-live',viewer_token='e'*32,checked_at=time.time())
            with self.assertRaises(ValueError):env.calibration.start('right',SN,profile='Alex',connected=connected)
            with self.assertRaises(ValueError):env.viewer.idle_check()
            self.assertEqual(env.commands,reads)
            env.spawn.assert_not_called();self.assertEqual(coordination.reservation(env.folder),env.marker)

    def test_local_profile_reads_still_wait_for_shutdown_and_software_maintenance(self):
        for condition in ('closing','runtime','software'):
            with self.subTest(condition=condition),scan_metadata_environment() as env:
                with ExitStack() as stack:
                    if condition=='closing':env.controller.desktop_closing=True
                    elif condition=='runtime':stack.enter_context(patch('managed_runtime.status',return_value=dict(busy=True)))
                    else:env.controller.official_tools.snapshot.return_value={'check':{'busy':True}}
                    state=env.calibration.snapshot(refresh=True)
                self.assertFalse(state['available']);self.assertFalse(state['metadata_current'])
                env.query.assert_not_called();env.prepare.assert_not_called();env.spawn.assert_not_called()
                self.assertEqual(coordination.reservation(env.folder),env.marker)


def export_browser_contract(path):
    """Generate the browser response by executing real snapshot/gate code."""
    with scan_metadata_environment() as env:
        state=env.calibration.snapshot(refresh=True)
        artifact=dict(scope='Actual production server gate and CalibrationCLI.snapshot with fake local-user query spy; no actual CLI/SDK/device process.',
            calibration=state,query_calls=[list(args) for args in env.commands],
            viewer=dict(scanning=env.viewer.state['scanning'],active=env.viewer.state['active'],device=env.viewer.state['device']),
            viewer_connection_record_calls=env.viewer.connection_record.call_count,actual_cli_process_calls=env.spawn.call_count,
            provider_calls=[dict(args=list(c.args),kwargs=c.kwargs) for c in env.provider.call_args_list],
            marker_kind=coordination.reservation(env.folder).get('kind'))
        Path(path).write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return artifact


if __name__=='__main__':unittest.main()
