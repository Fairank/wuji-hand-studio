"""Hand 2 feedback capture only; no actuator command API is used.

Raw effort is amperes. No simulated torque conversion or guessed command/context
is fed to the recognizer. Hardware inference awaits an explicit input mapping
and calibrated load transform, with fresh hardware-labelled validation.
"""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import time
from timing_stats import summarize_timing


def serialize_frame(frame, host_s):
    seq=frame.header.seq;stamp=frame.header.timestamp_us
    if type(seq) is not int or type(stamp) is not int:raise ValueError('Invalid sequence/timestamp')
    joints=[];ids=set()
    for entry in frame.joints:
        nid=entry.nid
        if type(nid) is not int or nid in ids:raise ValueError('Invalid or repeated joint nid')
        ids.add(nid)
        values=[float(entry.position),float(entry.velocity),float(entry.effort)]
        if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite joint feedback')
        joints.append(dict(nid=nid,position_rad=values[0],velocity_rad_s=values[1],effort_A=values[2]))
    if len(joints)!=int(frame.num_joints):raise ValueError('Frame joint count mismatch')
    return dict(seq=seq,device_timestamp_us=stamp,host_s=host_s,
                frame_id=str(frame.header.frame_id),joints=joints)


def require_left(hand):
    from device_profiles import require_identity
    return require_identity(hand)


def capture(hand, output, seconds, annotation='unlabelled'):
    from device_profiles import controller_profile
    selected=controller_profile()
    if selected['generation']!='hand2':raise ValueError('Use console recorder for Hand 1')
    require_left(hand)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'session.json').write_text(json.dumps(dict(
        side=selected['side'],generation='hand2',started=time.time(),seconds=seconds,annotation=annotation,
        online_joints=hand.online_joints_count().get(),read_only=True,
        sdk_version=importlib.metadata.version('wuji-sdk'),
        units=dict(position='rad',velocity='rad/s',effort='A'),
        recognition_enabled=False,reason='Requires real joint mapping, command context and load calibration',
        no_stream_rate_change=True),indent=2),encoding='utf-8')
    sub=hand.joint_states().subscribe()
    timing=[];device_timing=[];counts={};last_frame=time.monotonic();start=last_frame;last_status=start
    try:
        with (output/'feedback.jsonl').open('x',encoding='utf-8',buffering=1024*1024) as log:
            while time.monotonic()-start<seconds:
                frame=sub.recv()
                now=time.monotonic()
                if frame is None:
                    if now-last_frame>3:raise RuntimeError('No feedback for three seconds')
                    time.sleep(.0002);continue
                row=serialize_frame(frame,now);last_frame=now
                timing.append(dict(seq=row['seq'],host_s=now))
                device_timing.append(dict(seq=row['seq'],host_s=row['device_timestamp_us']/1e6))
                counts[len(row['joints'])]=counts.get(len(row['joints']),0)+1
                log.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
                if now-last_status>=1:
                    status=dict(frames=len(timing),elapsed_s=now-start,
                                observed_joint_count=len(row['joints']),read_only=True,
                                recognition_enabled=False)
                    (output/'status.json').write_text(json.dumps(status),encoding='utf-8')
                    last_status=now
    finally:
        sub.close()
        report=dict(host_arrival=summarize_timing(timing),device_timestamp_intervals=summarize_timing(device_timing),
                    joint_count_histogram=counts,no_motion_commands=True,
                    note='Timing and raw feedback only, not touch-recognition accuracy; nid order remains unmodified.')
        (output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--seconds',type=float,default=30)
    parser.add_argument('--address')
    parser.add_argument('--annotation',default='unlabelled')
    args=parser.parse_args()
    if not math.isfinite(args.seconds) or not 1<=args.seconds<=300:parser.error('Duration must be 1..300 seconds')
    from wuji_sdk import SdkManager,Handedness
    from device_profiles import controller_profile
    selected=controller_profile()
    if selected['generation']!='hand2':parser.error('Use console recorder for Hand 1')
    manager=SdkManager.instance()
    kwargs=dict(device_name='wuji_hand_2')
    if args.address:kwargs['address']=args.address
    else:kwargs['handedness']=Handedness.Left if selected['side']=='left' else Handedness.Right
    hand=manager.connect(**kwargs)
    try:print(json.dumps(capture(hand,args.output,args.seconds,args.annotation),ensure_ascii=False))
    finally:hand.disconnect()


if __name__=='__main__':main()
