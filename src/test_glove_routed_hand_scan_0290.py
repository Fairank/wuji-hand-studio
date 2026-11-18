"""Routed hand search keeps an already-open SDK glove streaming. No devices."""
import copy
from contextlib import nullcontext
import queue
import sys
import threading
import time
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock,patch

import device_discovery as discovery
from glove_agent import GloveSource,scan_preview_hands,worker_glove


class RoutedHandScanTests(unittest.TestCase):
    def test_managed_preview_reuses_existing_readonly_routed_scan(self):
        source=GloveSource();source.update([0.]*20,1,1,1.)
        manager=Mock();rows=[dict(serial='WH2KA00000000000',address='zenoh://WH2KA00000000000',generation='hand2')]
        with patch('zenoh_route.managed_wsl',return_value=True),patch.object(discovery,'scan_only',return_value=rows) as routed:
            self.assertEqual(scan_preview_hands(manager,object(),'receiving','sdk',source,1.1,None,{},False),rows)
        routed.assert_called_once();self.assertIs(routed.call_args.args[0],manager)
        manager.scan.assert_not_called();manager.connect.assert_not_called()

    def test_cancelled_routed_read_closes_routes_and_drops_late_advertisement(self):
        cancelled=[False];routes=[];ad=N(sn='WH2KA00000000000',address='zenoh://WH2KA00000000000',device_type='DeviceType.WujiHand2')
        class Route:
            def __init__(self,address,side):self.closed=False;routes.append(self)
            def open(self):pass
            def close(self):self.closed=True
        def scan():cancelled[0]=True;return [ad]
        manager=N(scan=scan,connect=Mock())
        with patch.object(discovery,'managed_wsl',return_value=True),patch.object(discovery,'DeviceRoute',Route):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                discovery.scan_only(manager,cancelled=lambda:cancelled[0])
        self.assertTrue(routes);self.assertTrue(all(route.closed for route in routes));manager.connect.assert_not_called()

    def test_route_cleanup_failure_retains_exact_routes_and_attempts_remaining_routes(self):
        error_type=getattr(discovery,'HandRouteCleanupUnconfirmed',None)
        self.assertTrue(isinstance(error_type,type),'Persistent search must retain unconfirmed route resources')
        first=N(close=Mock(side_effect=[False,None]));second=N(close=Mock(side_effect=[RuntimeError('Synthetic close failure'),None]))
        routes=discovery.Routes();routes.routes=[first,second]
        with self.assertRaises(error_type) as caught:routes.close()
        self.assertIs(caught.exception.bundle,routes);self.assertEqual(routes.routes,[first,second])
        self.assertEqual((first.close.call_count,second.close.call_count),(1,1))
        routes.close();self.assertEqual(routes.routes,[])

    def run_worker_search(self,*,cancel=False,fail_once=False,cleanup_failures=0,start_failure=False):
        commands=queue.Queue();events=[];calls=[];seq=[0];alternate=[False];phase=[0];current=[{'user_id':'Prior'}]
        started=threading.Event();release=threading.Event();fresh_while_held=[];searches=[]
        user={'user_id':'Wearer','display_name':'Wearer','is_default':False}
        ad=N(sn='WG-SYNTHETIC',address='example',device_type='DeviceType.WujiGlove')
        def recv():
            alternate[0]=not alternate[0]
            if not alternate[0]:return None
            seq[0]+=1
            return N(header=N(seq=seq[0],timestamp_us=seq[0]*1000),joints=[N(pose=N(position=[0.,0.,.02])) for _ in range(21)])
        glove=N(serial_number=ad.sn,hand_side=lambda:N(get=lambda:'right'),
            hand_skeleton=lambda:N(subscribe=lambda:N(recv=recv,close=lambda:calls.append('sub-close') or True)),
            disconnect=lambda:calls.append('glove-close') or True)
        manager=N(scan=Mock(return_value=[ad]),connect=Mock(return_value=glove),
            current_user=lambda:copy.deepcopy(current[0]),list_users=lambda:[{'user_id':'Prior'},user],
            switch_user=lambda uid:current.__setitem__(0,copy.deepcopy(user if uid=='Wearer' else {'user_id':'Prior'})))
        solver=N(step=lambda kp:[.1]*20,close=lambda:calls.append('solver-close') or True)
        sdk=N(SdkManager=N(instance=lambda:manager),DeviceType=N(WujiGlove='DeviceType.WujiGlove'),
            HandModel=N(WujiHand2='hand2',WujiHand='hand1'),Handedness=N(Left='left',Right='right'),
            RetargetSession=N(for_hand=Mock(return_value=solver)))
        route=N(close=Mock(side_effect=[False]*cleanup_failures+[None]))
        cleanup_bundle=discovery.Routes();cleanup_bundle.routes=[route]
        def routed(actual,*,cancelled=None,**kw):
            self.assertIs(actual,manager);self.assertTrue(callable(cancelled));searches.append(threading.get_ident());started.set()
            if fail_once and len(searches)==1:raise RuntimeError('Synthetic route search failed')
            while not release.wait(.005):
                if cancelled():raise RuntimeError('Hand discovery cancelled')
            if cancelled():raise RuntimeError('Hand discovery cancelled')
            if cleanup_failures:cleanup_bundle.close()
            return [dict(serial='WH2KA00000000000',address='zenoh://WH2KA00000000000',generation='hand2',side_hint='right')]
        owner=self
        class Events:
            def put(self,event):
                events.append(copy.deepcopy(event))
                if event.get('type')=='glove_closed' and event['session_cleanup']['retryable']:
                    commands.put({'name':'glove_disconnect'});return
                if event.get('type')!='glove_state':return
                state=event['state']
                if state.get('connection')!='receiving' or not state.get('stream',{}).get('fresh'):return
                if phase[0]==0:
                    phase[0]=1;commands.put({'name':'glove_hand_scan'})
                elif start_failure and state.get('hand_scan_error'):
                    phase[0]=3;commands.put({'name':'glove_disconnect'})
                elif fail_once and phase[0]==1 and state.get('hand_scan_error'):
                    phase[0]=2;commands.put({'name':'glove_hand_scan'})
                elif state.get('hand_scanning') and started.is_set() and not (fail_once and len(searches)==1):
                    fresh_while_held.append(state['stream']['seq'])
                    owner.assertFalse(state['can_prepare'])
                    if len(fresh_while_held)==1:
                        commands.put({'name':'glove_hand_scan'})
                        commands.put(dict(name='glove_prepare',serial='WH2KA00000000000',address=''))
                    if len(fresh_while_held)>=4:
                        if cancel:phase[0]=3;commands.put({'name':'glove_disconnect'})
                        else:release.set()
                elif state.get('hands') and not state.get('hand_scanning'):
                    phase[0]=3;commands.put({'name':'glove_disconnect'})
                elif cleanup_failures and state.get('hand_scan_error') and not state.get('hand_scanning'):
                    owner.assertFalse(state['can_prepare']);phase[0]=3
                    commands.put({'name':'glove_hand_scan'});commands.put({'name':'glove_disconnect'})
        commands.put(dict(name='glove_open',serial=ad.sn,user_id='Wearer',timeout_ms=250))
        timed_out=threading.Event()
        def abort():timed_out.set();release.set();commands.put({'name':'glove_disconnect'})
        timer=threading.Timer(4,abort);timer.daemon=True;timer.start()
        try:
            with patch.dict(sys.modules,wuji_sdk=sdk),patch.dict('os.environ',WUJI_HAND_PROFILE='hand2_right'),\
                    patch('zenoh_route.managed_wsl',return_value=True),patch.object(discovery,'scan_only',side_effect=routed),\
                    (patch('glove_agent.threading.Thread.start',side_effect=RuntimeError('Synthetic hand search thread unavailable')) if start_failure else nullcontext()):
                worker_glove(commands,Events())
        finally:timer.cancel();release.set()
        diagnostic=[(row.get('type'),row.get('message'),row.get('state',{}).get('message')) for row in events[-5:]]
        self.assertFalse(timed_out.is_set(),'Glove loop stalled during finite hand discovery: '+repr(diagnostic))
        self.assertEqual(phase[0],3,repr(diagnostic))
        if not start_failure:self.assertGreaterEqual(len(set(fresh_while_held)),4)
        manager.connect.assert_called_once_with(sn=ad.sn,device_name='studio_glove')
        self.assertEqual(len(searches),0 if start_failure else 2 if fail_once else 1)
        self.assertEqual(calls.count('glove-close'),1);self.assertEqual(calls.count('solver-close'),1)
        self.assertEqual(current[0],{'user_id':'Prior'})
        self.assertTrue(any(e.get('type')=='glove_notice' and 'search' in e.get('message','').lower() for e in events))
        self.assertTrue(events[-1]['session_cleanup']['glove_disconnected'])
        if cleanup_failures:
            self.assertEqual(route.close.call_count,cleanup_failures+1)
            self.assertEqual(cleanup_bundle.routes,[])
            self.assertFalse(any(row.get('state',{}).get('hands') for row in events if row.get('type')=='glove_state'))
            if cleanup_failures>1:self.assertTrue(any(row.get('session_cleanup',{}).get('retryable') for row in events if row.get('type')=='glove_closed'))
        return events

    def test_blocked_routed_search_keeps_glove_frames_and_rejects_duplicate_and_prepare(self):self.run_worker_search()
    def test_disconnect_cancels_same_search_without_opening_hand_or_reopening_glove(self):
        events=self.run_worker_search(cancel=True)
        self.assertFalse(any(e.get('state',{}).get('hands') for e in events if e.get('type')=='glove_state'))
    def test_failed_routed_search_retries_in_same_glove_and_solver(self):self.run_worker_search(fail_once=True)
    def test_unconfirmed_route_blocks_prepare_and_only_explicit_disconnect_retries_cleanup(self):
        for failures in (1,2):
            with self.subTest(failures=failures):self.run_worker_search(cleanup_failures=failures)
    def test_thread_start_failure_keeps_glove_owned_and_disconnect_cleanup_retryable(self):self.run_worker_search(start_failure=True)

    def test_preconnect_failed_route_keeps_worker_until_explicit_exact_cleanup(self):
        from glove_agent import paired_hand_worker
        address='192.0.2.60:7447';serial='WH2KA00000000000'
        ad=N(sn=serial,address='udp/'+address,device_type='DeviceType.WujiHand2')
        route=N(open=Mock(),close=Mock(side_effect=[False,False,None]))
        manager=N(scan=Mock(return_value=[ad]),connect=Mock(side_effect=RuntimeError('Synthetic connection refused')))
        sdk=N(SdkManager=N(instance=lambda:manager));requests=queue.Queue();records=queue.Queue();all_records=[]
        class Events:
            def put(self,value):all_records.append(copy.deepcopy(value));records.put(value)
        def await_cleanup():
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                row=records.get(timeout=2)
                if row.get('type')=='hand_session_closed':return row['cleanup_confirmed']
            self.fail('Missing exact hand cleanup observation')
        real_connect=discovery.connect_discovered
        def connect(actual,a,s):return real_connect(actual,a,s,use_routes=True)
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.object(discovery,'DeviceRoute',return_value=route),\
                patch.object(discovery,'connect_discovered',side_effect=connect):
            worker=threading.Thread(target=paired_hand_worker,args=(address,serial,{'id':'hand2_right'},requests,Events(),Mock()),daemon=True)
            worker.start()
            try:
                self.assertFalse(await_cleanup());self.assertTrue(worker.is_alive());self.assertEqual(route.close.call_count,1)
                requests.put({'name':'disconnect'});self.assertFalse(await_cleanup())
                self.assertTrue(worker.is_alive());self.assertEqual(route.close.call_count,2)
                requests.put({'name':'disconnect'});self.assertTrue(await_cleanup());worker.join(2)
                self.assertFalse(worker.is_alive());self.assertEqual(route.close.call_count,3)
            finally:
                if worker.is_alive():requests.put({'name':'disconnect'});worker.join(2)
        manager.connect.assert_called_once();self.assertTrue(any(row.get('type')=='closed' for row in all_records))

    def test_failed_identity_disconnect_retains_exact_hand_in_cleanup_exception(self):
        serial='WH2KA00000000000';hand=N(serial_number=serial,handedness=lambda:N(get=lambda:'left'),disconnect=Mock(return_value=False))
        manager=N(scan=Mock(return_value=[N(sn=serial,address='zenoh://'+serial,device_type='DeviceType.WujiHand2')]),connect=Mock(return_value=hand))
        with self.assertRaises(discovery.HandRouteCleanupUnconfirmed) as caught:
            discovery.connect_discovered(manager,serial=serial,use_routes=False)
        self.assertIs(caught.exception.hand,hand);self.assertEqual(caught.exception.bundle.routes,[])

    def test_delegated_driver_cleanup_failure_never_becomes_no_driver_stop_proof(self):
        from glove_agent import paired_hand_worker
        route=N(close=Mock(side_effect=[False,None]));bundle=discovery.Routes();bundle.routes=[route]
        hand=N(serial_number='WH1-SYNTHETIC');selected={'id':'hand1_right','generation':'hand1'}
        requests=queue.Queue();requests.put({'name':'disconnect'});records=[]
        sdk=N(SdkManager=N(instance=lambda:object()))
        with patch.dict(sys.modules,wuji_sdk=sdk),patch.object(discovery,'connect_discovered',return_value=(hand,bundle,selected)),\
                patch('first_generation.worker_first',return_value=None):
            paired_hand_worker('',hand.serial_number,selected,requests,N(put=lambda row:records.append(row)),Mock())
        self.assertFalse(records[-1]['cleanup_confirmed'])
        self.assertFalse(any(row.get('type')=='closed' for row in records))
        self.assertEqual(route.close.call_count,1)

    def test_partial_route_open_failure_retains_route_and_does_not_scan_or_fallback(self):
        route=N(open=Mock(side_effect=RuntimeError('Synthetic partial open failed')),close=Mock(return_value=False))
        manager=N(scan=Mock(),connect=Mock())
        with patch.object(discovery,'DeviceRoute',return_value=route),self.assertRaises(discovery.HandRouteCleanupUnconfirmed) as caught:
            discovery.connect_discovered(manager,'192.0.2.60:7447','WH2KA00000000000',use_routes=True)
        self.assertEqual(caught.exception.bundle.routes,[route]);self.assertIsNone(caught.exception.hand)
        route.close.assert_called_once();manager.scan.assert_not_called();manager.connect.assert_not_called()


if __name__=='__main__':unittest.main()
