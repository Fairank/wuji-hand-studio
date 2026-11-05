"""A stopped member's report must not invalidate live peers or fabricate an ACK."""
import copy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from group_coordinator import GroupCoordinator


class ReportCompletionTests(unittest.TestCase):
    def run_group(self, frames):
        """Use the real coordinator with endpoint DTOs at successive read barriers."""
        token='a'*32
        calls=[];reads={'L':0,'R':0};committed=False;clock=[100.]
        lock=threading.Lock()
        timing=dict(entry_s=.1,body_s=.2,exit_s=.1,hold_s=.1)
        def read(member):
            with lock:
                if not committed:
                    return dict(group=dict(token=token,mode='hardware',active=True,
                        phase='ready',clock_s=clock[0],clock_id='synthetic-shared-clock',timing=timing))
                index=min(reads[member],len(frames)-1);reads[member]+=1
                row=frames[index][member]
                group=dict(token=token,mode='hardware',active=False,completed=True,stop_confirmed=True,phase='completed',
                    clock_s=clock[0],elapsed_s=.5,control_version=1)
                group.update(row.get('group',{}))
                hardware=dict(active=False,stop_confirmed=True,start_pending=False)
                hardware.update(row.get('hardware',{}))
                return dict(connection=row.get('connection','connected'),stale=row.get('stale',True),
                    group=group,hardware=hardware)
        def post(member,command):
            nonlocal committed
            with lock:
                calls.append((member,copy.deepcopy(command)))
                if command['name']=='group_commit':committed=True
            return {}
        def sleep(seconds):clock[0]+=seconds
        coordinator=GroupCoordinator(read,post)
        coordinator.token=token;coordinator.heartbeat=clock[0]
        coordinator.state=dict(active=True,phase='preparing',members=[dict(id=i,phase='preparing') for i in reads])
        spec=dict(members=['L','R'],mode='hardware',kind='pair',action='pair_wave',
            speed=1,cycles=1,amplitude=.5,workspace_clear=True)
        try:
            with patch('group_coordinator.time',SimpleNamespace(monotonic=lambda:clock[0],sleep=sleep)):
                coordinator.run(spec,token)
            result=coordinator.snapshot()
            self.assertFalse(result['active'])
            aborts=[(member,command) for member,command in calls if command['name']=='group_abort']
            self.assertEqual({member for member,_ in aborts},{'L','R'})
            self.assertTrue(all(command['token']==token for _,command in aborts))
            return result,reads
        finally:coordinator.pool.shutdown(wait=True)

    def test_exact_completed_stop_ack_survives_report_aged_feedback(self):
        result,_=self.run_group([{'L':{},'R':{}}])
        self.assertEqual(result['phase'],'completed')
        self.assertEqual(result['stop_unconfirmed'],[])

    def test_completed_member_may_age_while_live_peer_stays_fresh(self):
        for stopped,live in (('L','R'),('R','L')):
            with self.subTest(stopped=stopped):
                result,reads=self.run_group([
                    {stopped:{},live:dict(stale=False,group=dict(phase='playing',active=True,completed=False,stop_confirmed=False),hardware=dict(active=True,stop_confirmed=False))},
                    {'L':{},'R':{}},
                ])
                self.assertEqual(result['phase'],'completed')
                self.assertGreaterEqual(min(reads.values()),2)
                self.assertEqual(result['stop_unconfirmed'],[])

    def test_finished_peer_does_not_excuse_a_stale_live_hand(self):
        result,_=self.run_group([{'L':{},'R':dict(group=dict(phase='playing',active=True,completed=False,stop_confirmed=False),hardware=dict(active=True,stop_confirmed=False))}])
        self.assertEqual(result['phase'],'failed')
        self.assertIn('feedback expired',result['reason'])
        self.assertEqual(result['stop_unconfirmed'],['R'])

    def test_terminal_exception_requires_real_ack_and_current_hardware_group(self):
        invalid=[
            dict(group=dict(token='b'*32)),dict(group=dict(mode='preview')),
            dict(group=dict(completed=False)),dict(group=dict(completed=1)),
            dict(group=dict(active=True)),dict(group=dict(active=0)),
            dict(group=dict(stop_confirmed=False)),dict(group=dict(stop_confirmed=1)),
            dict(group=dict(phase='returning')),dict(hardware=dict(active=True)),
            dict(hardware=dict(active=None)),dict(hardware=dict(active=0)),
            dict(hardware=dict(stop_confirmed=False)),dict(hardware=dict(stop_confirmed=None)),
            dict(hardware=dict(stop_confirmed=1)),dict(hardware=dict(start_pending=True)),
            dict(hardware=dict(start_pending=None)),dict(hardware=dict(start_pending=0)),
        ]
        for row in invalid:
            with self.subTest(row=row):
                result,_=self.run_group([{'L':{},'R':row}])
                self.assertEqual(result['phase'],'failed')

    def test_fresh_complete_compatibility_still_finishes(self):
        result,_=self.run_group([{'L':dict(stale=False),'R':dict(stale=False)}])
        self.assertEqual(result['phase'],'completed')


if __name__=='__main__':unittest.main()
