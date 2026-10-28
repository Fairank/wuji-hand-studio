from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from native_task_windows import TaskWindows


class Event:
    def __init__(self): self.callbacks=[]
    def __iadd__(self, callback): self.callbacks.append(callback); return self
    def fire(self):
        for callback in list(self.callbacks): callback()


class Window:
    def __init__(self):
        self.events=SimpleNamespace(loaded=Event(),closed=Event())
        self.restore=Mock();self.show=Mock();self.destroy=Mock(side_effect=self.events.closed.fire)


class TaskWindowTests(unittest.TestCase):
    def setUp(self):
        self.ident='a'*24
        self.host=SimpleNamespace(origin='http://127.0.0.1:8781',closing=False,allow_close=False,
            window=Mock(),server=Mock(),secure_window=Mock(),info=Mock(return_value={'native':True}))
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=self.ident,status='connected',label='Glove') ]}
        self.factory=Mock(side_effect=lambda *args,**kwargs:Window())
        self.manager=TaskWindows(self.host,self.factory)

    def test_open_reuse_close_and_reopen_never_dispatches_device_command(self):
        self.assertFalse(self.manager.open(self.ident)['reused'])
        args,options=self.factory.call_args
        self.assertEqual(args[1],'http://127.0.0.1:8781/?desktop=1&task_window='+self.ident+'#tasks')
        first=self.manager.windows[self.ident]
        first.events.loaded.fire();self.host.secure_window.assert_called_once_with(first)
        self.assertTrue(self.manager.open(self.ident)['reused']);self.assertEqual(self.factory.call_count,1)
        options['js_api'].close_task_window(self.ident)
        self.assertEqual(self.manager.snapshot()['task_ids'],[])
        self.assertFalse(self.manager.open(self.ident)['reused'])
        self.assertEqual(self.factory.call_count,2)
        self.host.server.controller.action.assert_not_called()
        self.assertIn('task-window-closed',' '.join(call.args[0] for call in self.host.window.evaluate_js.call_args_list))

    def test_invalid_unknown_disconnected_duplicate_and_closing_tasks_do_not_open(self):
        for bad in (None,False,'https://example.com','../task','a'*25):
            with self.assertRaises(ValueError):self.manager.open(bad)
        with self.assertRaises(ValueError):self.manager.open('b'*24)
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=self.ident,status='disconnected')]}
        with self.assertRaises(ValueError):self.manager.open(self.ident)
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=self.ident,status='connected')]*2}
        with self.assertRaises(ValueError):self.manager.open(self.ident)
        self.factory.assert_not_called()

    def test_api_cannot_close_other_task_or_main_window(self):
        self.manager.open(self.ident);api=self.factory.call_args.kwargs['js_api']
        self.assertFalse(hasattr(api,'request_close'));self.assertFalse(hasattr(api,'stop_and_close'))
        with self.assertRaises(ValueError):api.close_task_window('b'*24)
        self.assertEqual(self.manager.snapshot()['task_ids'],[self.ident])

    def test_create_failure_can_be_retried_and_main_shutdown_closes_views_only(self):
        self.factory.side_effect=RuntimeError('window unavailable')
        with self.assertRaises(RuntimeError):self.manager.open(self.ident)
        self.assertEqual(self.manager.snapshot()['task_ids'],[])
        self.factory.side_effect=lambda *a,**k:Window()
        self.manager.open(self.ident);window=self.manager.windows[self.ident]
        self.manager.shutdown();window.destroy.assert_called_once()
        self.assertEqual(self.manager.snapshot()['task_ids'],[])
        with self.assertRaises(ValueError):self.manager.open(self.ident)
        self.host.server.controller.action.assert_not_called()

    def test_bounded_windows_and_unknown_recovery_view_allowed(self):
        rows=[dict(task_id=f'{i:024x}',status='unknown') for i in range(9)]
        self.host.server.tasks.snapshot.return_value={'tasks':rows}
        for row in rows[:8]:self.manager.open(row['task_id'])
        with self.assertRaises(ValueError):self.manager.open(rows[8]['task_id'])
        self.assertEqual(self.factory.call_count,8)

    def test_verified_switch_rebinds_same_window_and_close_api_to_new_task(self):
        self.manager.open(self.ident);window=self.manager.windows[self.ident]
        api=self.factory.call_args.kwargs['js_api'];target='b'*24
        receipt=dict(version=1,source_task_id=self.ident,target_task_id=target,after_context='c'*64)
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=target,status='connected',label='New hand')]}
        proof=dict(current=True,owner_context='c'*64,last_transition_receipt=receipt)
        with patch('task_view.scope',return_value=proof):
            api.rebind_task_window(self.ident,target,receipt)
        self.assertEqual(self.manager.snapshot()['task_ids'],[target]);self.assertEqual(self.factory.call_count,1)
        with self.assertRaises(ValueError):api.close_task_window(self.ident)
        api.close_task_window(target);self.assertEqual(self.manager.snapshot()['task_ids'],[])
        window.destroy.assert_called_once();self.host.server.controller.action.assert_not_called()

    def test_unverified_or_other_endpoint_switch_preserves_original_window(self):
        self.manager.open(self.ident);target='b'*24
        receipt=dict(version=1,source_task_id=self.ident,target_task_id=target,after_context='c'*64)
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=target,status='connected')]}
        for proof in ({'current':False},{'current':True,'owner_context':'d'*64,'last_transition_receipt':receipt},
                      {'current':True,'owner_context':'c'*64,'last_transition_receipt':dict(receipt,version=True)}):
            with patch('task_view.scope',return_value=proof),self.assertRaises(ValueError):
                self.manager.rebind(self.ident,target,receipt)
            self.assertEqual(self.manager.snapshot()['task_ids'],[self.ident])
        self.host.server.tasks.snapshot.return_value={'tasks':[dict(task_id=target,status='connected',endpoint_id='other')]}
        with self.assertRaises(ValueError):self.manager.rebind(self.ident,target,receipt)


if __name__=='__main__':unittest.main()
