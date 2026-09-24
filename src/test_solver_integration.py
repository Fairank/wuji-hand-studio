import copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from retarget_tuning import TuningStore,values_hash
from glove_protocol import validate
from calibration_cli import record_progress

class SolverIntegrationTests(unittest.TestCase):
    def test_packaged_controller_uses_executable_directory(self):
        import managed_runtime as m
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'controller/source';source.mkdir(parents=True)
            (source/'agent_bootstrap.py').write_text('# fixture')
            with patch.object(m.sys,'frozen',True,create=True),patch.object(m.sys,'executable',str(root/'HandWorkbench.exe')),patch.object(m,'check_ready'),patch.object(m,'Files'),patch('controller_bundle.ensure_version',return_value='/opt/hand-workbench/code/fixture') as deploy,patch('controller_launch.parameter_environment',return_value='TEST=1'):
                m.WslController({},'hand2_left')
                self.assertEqual(deploy.call_args.args[0],source)

    def test_empty_controller_bundle_is_not_a_successful_deployment(self):
        from controller_bundle import ensure_version
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError,'empty'):ensure_version(td,None)
            with self.assertRaisesRegex(ValueError,'does not exist'):ensure_version(Path(td)/'missing',None)

    def test_empty_solver_status_reports_underlying_failure(self):
        from solver_runtime import SolverRuntime
        from subprocess import CompletedProcess
        with patch('bridge_config.load_config',return_value=dict(mode='local',agent_directory='/fixture',python='python3')),patch('calibration_cli.controller_args',return_value=['fixture']),patch('solver_runtime.subprocess.run',return_value=CompletedProcess([],2,'','missing setup script')):
            runtime=SolverRuntime();runtime.run(False)
            self.assertFalse(runtime.snapshot()['ready'])
            self.assertEqual(runtime.snapshot()['error'],'missing setup script')

    def binding(self,side='left'):
        return dict(generation='hand2',side=side,glove_serial='WGTEST',hand_serial='HANDTEST',sdk_user='tester')

    def test_engine_is_per_binding_and_old_drafts_default_sdk(self):
        with tempfile.TemporaryDirectory() as td:
            store=TuningStore(td);a=self.binding();b=self.binding('right')
            self.assertEqual(store.engine(a),'sdk');store.select_engine(a,'official_open')
            self.assertEqual(TuningStore(td).engine(a),'official_open');self.assertEqual(store.engine(b),'sdk')
            with self.assertRaises(ValueError):store.select_engine(a,'arbitrary_program')

    def test_applied_identity_changes_only_for_parameter_values(self):
        with tempfile.TemporaryDirectory() as td:
            s=TuningStore(td);a=s.context(self.binding());b=copy.deepcopy(a['values'])
            b['segment_scaling']['index'][0]=.75
            self.assertNotEqual(values_hash(b),a['values_sha256'])
            self.assertEqual(values_hash(dict(reversed(list(a['values'].items())))),a['values_sha256'])

    def test_command_surface_rejects_programs_paths_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as td:
            values=TuningStore(td).context(self.binding())['values']
            validate(dict(name='glove_solver',solver=dict(engine='official_open',values=values)))
            for solver in [dict(engine='external',values=values),dict(engine='official_open',values=values,path='/tmp/run'),dict(engine='official_open',values={})]:
                with self.assertRaises(ValueError):validate(dict(name='glove_solver',solver=solver))
            with self.assertRaises(ValueError):validate(dict(name='glove_solver',solver=dict(engine='sdk'),q=[0]*20))

    def test_pose_completion_requires_official_done_not_index_or_full_progress(self):
        run={}
        record_progress(run,dict(step_index=3,step_total=6,progress=1,state='collecting'))
        self.assertEqual(run['captured_poses'],[])
        record_progress(run,dict(step_index=3,step_total=6,state='done'))
        record_progress(run,dict(step_index=3,step_total=6,state='done'))
        self.assertEqual(run['captured_poses'],[3])
        record_progress(run,dict(step_name='unknown',step_index=4,step_total=6,state='done'))
        self.assertEqual(run['captured_poses'],[3])

    def test_selection_submits_preview_only_and_does_not_claim_application(self):
        from console_server import Controller
        with tempfile.TemporaryDirectory() as td:
            c=Controller(factory=lambda:None,reports=Path(td)/'reports')
            binding=c.retarget.context(c.state['device_profile'])['binding'];ctx=c.tuning.context(binding)
            with patch.object(c.glove,'snapshot',return_value=dict(connection='receiving',feedback={},hardware={})),patch.object(c.glove,'action') as action:
                result=c.action(dict(name='retarget_engine_select',binding=binding,engine='official_open',values=ctx['values'],revision=0))
                self.assertFalse(result['runtime_applied']);self.assertTrue(result['submitted'])
                self.assertEqual(action.call_args.args[0]['name'],'glove_solver')
            with patch.object(c.glove,'snapshot',return_value=dict(connection='receiving',feedback=dict(device_id='test'),hardware={})),patch.object(c.glove,'action') as action:
                with self.assertRaises(ValueError):c.action(dict(name='retarget_engine_select',binding=binding,engine='sdk',revision=1))
                action.assert_not_called()
            self.assertFalse(c.state['hardware']['active'])

if __name__=='__main__':unittest.main()
