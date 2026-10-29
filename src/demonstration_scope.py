"""Four local demonstration players; no SDK owner or physical command access."""
import copy
import re
import threading

from demo_player import DemoPlayer
from device_profiles import PROFILES,profile

COMMANDS={'demo_start','demo_pause','demo_resume','demo_stop'}


def display_profile(value):
    if not isinstance(value,str) or value not in PROFILES:
        raise ValueError('请选择有效的展示手型 / Choose a valid demonstration hand model')
    return profile(value)


def query_profile(query,*,default=None):
    values=query.get('profile')
    if values is None:return default
    if not isinstance(values,list) or len(values)!=1:
        raise ValueError('展示手型参数不唯一 / Demonstration profile must be specified once')
    return display_profile(values[0])['id']


def require_expected_profile(command,active_profile,active_serial=None):
    if command.get('name')=='hardware_trial' and {'expected_profile','expected_serial'}&set(command):
        if not {'expected_profile','expected_serial'}<=set(command):
            raise ValueError('请重新选择展示手型和目标设备 / Select the demonstration profile and target hand together')
        expected=display_profile(command['expected_profile'])['id']
        serial=command['expected_serial']
        if not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',serial):
            raise ValueError('目标灵巧手身份无效，请重新选择 / Target hand serial is invalid; select it again')
        if expected!=active_profile:
            raise ValueError('已连接的灵巧手与展示手型不符，请先连接所选型号 / Connected hand differs from the selected demonstration profile')
        if serial!=active_serial:
            raise ValueError('已连接的灵巧手已变化，请重新选择目标设备 / Connected hand changed; select the target device again')


class DemonstrationScope:
    def __init__(self):
        self.lock=threading.RLock()
        self.players={name:DemoPlayer() for name in PROFILES}

    def snapshot(self,profile_id=None):
        if profile_id is not None:
            key=display_profile(profile_id)['id']
            with self.lock:return dict(self.players[key].snapshot(),display_profile=key)
        with self.lock:
            return {key:dict(player.snapshot(),display_profile=key) for key,player in self.players.items()}

    def command(self,command):
        if not isinstance(command,dict) or command.get('name') not in COMMANDS:
            raise ValueError('Unknown scoped demonstration command')
        name=command['name'];key=display_profile(command.get('display_profile'))['id']
        allowed=({'name','display_profile','action','speed','cycles','text'} if name=='demo_start'
                 else {'name','display_profile','expected_run'})
        if set(command)-allowed:raise ValueError('Invalid scoped demonstration request')
        with self.lock:
            self.players[key].command({k:v for k,v in command.items() if k!='display_profile'})
            return dict(demonstration_playback=self.snapshot())


def demonstration_state(state,profile_id,playback,display=None):
    desired=display_profile(profile_id)
    if display not in (None,'feedback','preview'):
        raise ValueError('Unknown demonstration display source')
    result=copy.deepcopy(state)
    same=(result.get('device_profile') or {}).get('id')==desired['id']
    preview=(display=='preview' or display is None and (playback.get('active') is True or not same))
    shown=copy.deepcopy(playback)
    if display=='feedback':shown.update(active=False,running=False)
    result.update(device_profile=desired,playback=shown,
                  view_source='preview' if preview else 'feedback',
                  glove={'busy':False,'connection':'disconnected','stream':{},'hardware':{},'feedback':{}},
                  demonstration_profile=desired['id'],demonstration_preview_only=preview,demonstration_display=display)
    if preview or not same:
        result.update(connection='disconnected',latest=None,stale=True,mapping=[],mapping_device_id=None,
                      mapping_verified=False,device_id=None,identified_device=None,joint_rates=[],
                      metrics={'host_hz':None,'device_hz':None,'age_ms':None},
                      hardware={'active':False},program={'active':False},group={'active':False})
    return result
