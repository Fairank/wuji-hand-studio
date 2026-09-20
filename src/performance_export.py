"""Portable authored trajectory export, explicitly NOT measured execution data."""
import csv
import io
import math
from performance_program import program, sample


def export_program(action,text,profile_id,format='json'):
    from device_profiles import profile,performance_preview_pose
    from hardware_trial import LABELS
    p=profile(profile_id);data=program(action,text)
    def adapted(q):
        return performance_preview_pose(q,profile_id)
    result=dict(data,device_profile=profile_id,units=dict(time='s',position='rad',velocity='rad/s'),
        joints=LABELS,basis='nominal_authored_kinematic_targets_not_measured_feedback',
        hardware_requires='Hand2 controller v2, measured-start rebasing, amplitude and configured-speed retiming',
        hand1_preview_only=p['generation']=='hand1',
        points=[dict(k,q=adapted(k['q']),**({'v':[(-v if i==1 and k['q'][1]<0 and p['generation']=='hand1' else v) for i,v in enumerate(k['v'])]} if 'v' in k else {})) for k in data['points']])
    if p['generation']=='hand1':
        # Native-range clipping is nonlinear. Export actual sampled preview targets
        # instead of attaching unclipped Hermite derivatives to clipped positions.
        result['points']=[dict(t=min(k/100,data['duration_s']),q=adapted(sample(action,min(k/100,data['duration_s']),text,loop=False)['q']),interpolation='linear') for k in range(math.ceil(data['duration_s']*100-1e-7)+1)]
        result['preview_mapping']='thumb abduction absolute value; clip to native actuator ranges; not retargeting calibration'
    if format=='json':return result
    if format!='csv':raise ValueError('Use csv or json')
    output=io.StringIO(newline='');writer=csv.writer(output)
    writer.writerow(['time_s','profile','basis','phase','token_index']+LABELS)
    # The authored clock uses centiseconds. Ignore floating addition residue
    # so 4.760000000000001 does not create two CSV rows labelled 4.760.
    count=math.ceil(data['duration_s']*1000-1e-7)
    for k in range(count+1):
        t=min(k/1000,data['duration_s']);row=sample(action,t,text,loop=False)
        writer.writerow([f'{t:.3f}',profile_id,'nominal_not_measured',row['label'],row['token_index']]+[f'{x:.7f}' for x in adapted(row['q'])])
    return ('\ufeff'+output.getvalue()).encode('utf-8')
