"""Desktop exit coordination; never disconnect before an active stop is confirmed."""
import time

def needs_confirmation(state):
    return (state.get('connection')!='disconnected' or state.get('glove',{}).get('busy',False)
            or state.get('recording',{}).get('active',False)
            or state.get('hardware',{}).get('active') is not False)

def close_sessions(controller, timeout=6., clock=time.monotonic, sleep=time.sleep):
    state=controller.snapshot()
    if state.get('parameter_sync',{}).get('busy') or controller.doctor.snapshot().get('running'):
        return dict(ok=False,error='请等待参数同步或诊断结束。 / Wait for parameter sync or diagnostics.')
    glove=state.get('glove',{}); hardware=glove.get('hardware',{}) if glove.get('busy') else state.get('hardware',{})
    active=hardware.get('active',False)
    if active is not False:
        controller.action(dict(name='hardware_stop'))
        deadline=clock()+timeout
        while clock()<deadline:
            state=controller.snapshot()
            hw=state.get('glove',{}).get('hardware',{}) if glove.get('busy') else state.get('hardware',{})
            if hw.get('active') is False and hw.get('stop_confirmed') is True:break
            sleep(.05)
        else:return dict(ok=False,error='尚未确认机械手停用，请回到工作台检查连接。 / Hand disable is not confirmed; check the connection.')
    if glove.get('busy'):
        controller.action(dict(name='glove_disconnect'))
        deadline=clock()+timeout
        while controller.glove.busy and clock()<deadline:sleep(.05)
        if controller.glove.busy:return dict(ok=False,error='手套会话仍在结束，请稍后重试。 / Glove session is still closing; retry shortly.')
    controller.player.command(dict(name='demo_stop'))
    controller.action(dict(name='disconnect'))
    # Existing SDK disconnect gives recording flush and disable a grace period.
    if state.get('connection')!='disconnected':sleep(3.1)
    return dict(ok=True)
