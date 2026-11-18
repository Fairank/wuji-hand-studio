"""Exact reservation-release receipts do not fabricate a hardware disable ACK."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from group_coordinator import GroupCoordinator

TOKEN='a'*32


class ReadonlyCleanupTests(unittest.TestCase):
    def run_cancel(self,receipts,hardware=None,errors=()):
        calls=[];clock=[100.];reads=[]
        hardware=hardware or {}
        coordinator=None
        def post(member,command):
            calls.append((member,copy.deepcopy(command)))
            if command['name']=='group_prepare':coordinator.cancel.set();return {}
            self.assertEqual(command['name'],'group_abort')
            if member in errors:raise OSError('Synthetic abort reply missing')
            return copy.deepcopy(receipts.get(member,{}))
        def read(member):
            reads.append(member)
            return dict(hardware=copy.deepcopy(hardware.get(member,dict(active=False,stop_confirmed=False))))
        def sleep(seconds):clock[0]+=seconds
        coordinator=GroupCoordinator(read,post)
        coordinator.token=TOKEN;coordinator.heartbeat=clock[0]
        coordinator.state=dict(active=True,phase='preparing',members=[dict(id=i,phase='preparing') for i in ('L','R')])
        spec=dict(members=['L','R'],mode='hardware',kind='pair',action='pair_wave',speed=1,cycles=1,amplitude=.5,workspace_clear=True)
        try:
            with patch('group_coordinator.time',SimpleNamespace(monotonic=lambda:clock[0],sleep=sleep)):
                coordinator.run(spec,TOKEN)
            self.assertFalse(any(command['name']=='group_arm' for _,command in calls))
            self.assertEqual({member for member,command in calls if command['name']=='group_abort'},{'L','R'})
            return coordinator.snapshot(),reads
        finally:coordinator.pool.shutdown(wait=True)

    def receipt(self,**changes):
        group=dict(stopping=True,token=TOKEN,mode='hardware',read_only_released=True)
        group.update(changes)
        return dict(group=group)

    def test_exact_readonly_release_needs_no_fabricated_motor_ack(self):
        result,reads=self.run_cancel({member:self.receipt() for member in ('L','R')})
        self.assertEqual(result['phase'],'stopped')
        self.assertEqual(result['stop_unconfirmed'],[])
        self.assertEqual(reads,[])

    def test_readonly_and_real_disable_ack_are_separate_per_member(self):
        for readonly,stopped in (('L','R'),('R','L')):
            with self.subTest(readonly=readonly):
                result,reads=self.run_cancel({readonly:self.receipt()},
                    {stopped:dict(active=False,stop_confirmed=True)})
                self.assertEqual(result['stop_unconfirmed'],[])
                self.assertEqual(reads,[stopped])

    def test_no_receipt_retains_original_unknown_even_beside_readonly_peer(self):
        result,reads=self.run_cancel({'L':self.receipt()},
            {'R':dict(active=None,stop_confirmed=False)})
        self.assertEqual(result['stop_unconfirmed'],['R'])
        self.assertTrue(reads);self.assertEqual(set(reads),{'R'})

    def test_wrong_token_mode_flag_or_envelope_cannot_release_uncertainty(self):
        invalid=[self.receipt(token='b'*32),self.receipt(mode='preview'),
            self.receipt(read_only_released=False),self.receipt(read_only_released=None),
            self.receipt(read_only_released=1),self.receipt(stopping=False),
            self.receipt(stopping=1),dict(read_only_released=True,token=TOKEN,mode='hardware'),
            dict(group=True),None]
        for receipt in invalid:
            with self.subTest(receipt=receipt):
                result,_=self.run_cancel({'L':self.receipt(),'R':receipt})
                self.assertEqual(result['stop_unconfirmed'],['R'])

    def test_lost_release_reply_keeps_cleanup_failure_visible(self):
        result,_=self.run_cancel({'L':self.receipt(),'R':self.receipt()},
            {'R':dict(active=False,stop_confirmed=True)},errors={'R'})
        self.assertEqual(result['stop_unconfirmed'],['R'])
        self.assertIn('Stop unconfirmed',result['reason'])


if __name__=='__main__':unittest.main()
