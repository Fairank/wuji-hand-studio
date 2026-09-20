"""Local numeric edits and explicit file synchronization, never hardware I/O."""
import hashlib
import json
import os
from pathlib import Path
import time
from parameter_file import parse_parameters,render_parameters

FIELDS=[
    dict(key='KP',group='motor',label='位置增益 Kp',unit='',description='追目标位置的响应强度；调大通常更硬。',advice='官方建议 ≥ 3；未提供推荐上限',initial=5.),
    dict(key='KD',group='motor',label='阻尼增益 Kd',unit='',description='当前主要抑制运动与振荡；不是速度上限。',advice='官方建议 0.01～0.05',initial=.01),
    dict(key='CURRENT_LIMIT_A',group='motor',label='电流上限',unit='A',description='限制可输出电流，不等同于接触力。',advice='官方建议 ≤ 1.5 A；设备硬上限 2 A',initial=.5),
    dict(key='PATH_SPEED_RAD_S',group='speed',label='轨迹规划速度',unit='rad/s',description='按关节位移计算每段动作的时间。',advice='项目设置；0.075 rad/s ≈ 4.30°/s',initial=.075),
    dict(key='COMMAND_SPEED_RAD_S',group='speed',label='指令变化速率',unit='rad/s',description='每次位置目标允许变化多快；与规划速度共同作用。',advice='项目设置；网页速度档还会缩放',initial=.08),
    dict(key='COMMAND_RATE_HZ',group='speed',label='指令发送频率',unit='Hz',description='电脑每秒向手发布多少次目标指令；不修改电机内部控制频率，也不等于网页帧率。',advice='常用 100 / 250 / 500 / 1000；官方PUB最高1000 Hz，实测节拍另行显示',initial=1000.),
    dict(key='PROBE_SPEED_RAD_S',group='speed',label='单关节检查速度',unit='rad/s',description='3°往返检查的指令变化上限。',advice='项目设置；不自动缩短检查轨迹时间',initial=.05),
    dict(key='MIN_TRANSITION_S',group='time',label='最短过渡时间',unit='s',description='动作段或起点衔接至少用多少秒。',advice='项目设置；还受位移和速度影响',initial=1.),
    dict(key='POSE_HOLD_S',group='time',label='候选姿态停留',unit='s',description='张开、轻握等候选目标的停留时间。',advice='项目设置；网页慢速档会延长',initial=1.),
    dict(key='OFFICIAL_ENDPOINT_HOLD_S',group='time',label='官方录制端点停留',unit='s',description='官方录制首帧和末帧的额外停留。',advice='项目适配设置，不改原录制姿态',initial=.5),
    dict(key='OFFICIAL_RETURN_HOLD_S',group='time',label='返回起点后停留',unit='s',description='官方录制返回本次实测起点后的停留。',advice='项目设置，单位秒',initial=1.),
    dict(key='MAX_TRIAL_DURATION_S',group='time',label='最长计划时长',unit='s',description='包含所选循环的一次完整播放时长。',advice='项目播放设置，不是官方故障阈值',initial=600.)]


def digest(source):return hashlib.sha256(source.encode('utf-8')).hexdigest()


class ParameterStore:
    def __init__(self,path):
        self.path=Path(path);self.history=self.path.parent/'parameter_history'
        self.sync_status=dict(busy=False,state='not_synced',message='尚未通过网页同步',revision=None)
        record=self.history/'web-last-sync.json'
        if record.exists():
            try:self.sync_status.update(json.loads(record.read_text(encoding='utf-8')),busy=False)
            except (OSError,ValueError):pass

    def source(self):return self.path.read_text(encoding='utf-8-sig')

    def snapshot(self):
        try:
            source=self.source()
            return dict(values=parse_parameters(source),revision=digest(source),fields=FIELDS,sync=dict(self.sync_status),error=None)
        except (OSError,ValueError,SyntaxError) as e:
            return dict(values=None,revision=None,fields=FIELDS,sync=dict(self.sync_status),error=str(e))

    def save(self,values,revision):
        if self.sync_status['busy']:raise ValueError('正在同步，请稍后保存')
        source=self.source()
        if revision!=digest(source):raise ValueError('参数文件已被其他窗口或代码修改；请重新读取后再保存')
        updated=render_parameters(source,values)
        self.history.mkdir(exist_ok=True)
        (self.history/(str(time.time_ns())+'-before-web.py')).write_text(source,encoding='utf-8')
        staged=self.path.with_suffix('.py.saving');staged.write_text(updated,encoding='utf-8');os.replace(staged,self.path)
        return self.snapshot()

    def begin_sync(self,revision):
        if self.sync_status['busy']:raise ValueError('已有参数同步正在进行')
        source=self.source();parse_parameters(source)
        if revision!=digest(source):raise ValueError('本机参数有更新，请重新读取后同步')
        self.sync_status=dict(busy=True,state='syncing',message='正在同步参数文件；不会启动机械手',revision=None)
        return source

    def transfer(self,client,source):
        REMOTE=client.agent_directory+'/motion_parameters.py'
        raw=source.encode('utf-8')
        with client.open_sftp() as sftp:
            if hasattr(sftp,'get_channel'):sftp.get_channel().settimeout(15)
            with sftp.open(REMOTE,'rb') as previous:old=previous.read()
            self.history.mkdir(exist_ok=True)
            (self.history/(str(time.time_ns())+'-before-web-remote.py')).write_bytes(old)
            with sftp.open(REMOTE+'.upload','wb') as target:target.write(raw)
            sftp.posix_rename(REMOTE+'.upload',REMOTE)
            with sftp.open(REMOTE,'rb') as target:
                if target.read()!=raw:raise ValueError('远端参数文件校验不符')
            cache=client.agent_directory+'/__pycache__'
            try:entries=sftp.listdir(cache)
            except FileNotFoundError:entries=[]
            for name in entries:
                if name.startswith('motion_parameters.cpython-') and name.endswith('.pyc') and '/' not in name and '\\' not in name:
                    sftp.remove(cache+'/'+name)

    def finish_sync(self,source,error=None):
        self.sync_status=dict(busy=False,state='failed' if error else 'synced',revision=None if error else digest(source),
            message='同步失败，未启动设备：'+str(error) if error else '已同步；重新连接后加载，动作仍需手动启动')
        if not error:
            self.history.mkdir(exist_ok=True)
            (self.history/'web-last-sync.json').write_text(json.dumps(self.sync_status,ensure_ascii=False),encoding='utf-8')
