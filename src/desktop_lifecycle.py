"""Desktop exit coordination; never disconnect before an active stop is confirmed."""
import time
from device_termination import unconfirmed

def needs_confirmation(state):
    return (state.get('connection')!='disconnected' or state.get('glove',{}).get('busy',False)
            or state.get('official_studio',{}).get('reserved',False) or state.get('official_studio',{}).get('busy',False)
            or state.get('official_studio',{}).get('active',False)
            or state.get('official_viz',{}).get('active',False) or state.get('official_viz',{}).get('busy',False)
            or state.get('official_viz',{}).get('reserved',False)
            or state.get('recording',{}).get('active',False)
            or state.get('calibration',{}).get('running',False)
            or state.get('calibration_connection',{}).get('busy',False)
            or state.get('teleoperation_connection',{}).get('busy',False)
            or state.get('calibrated_teleoperation',{}).get('busy',False)
            or state.get('hardware',{}).get('active') is not False or bool(unconfirmed(state)))

def close_sessions(controller, timeout=6., clock=time.monotonic, sleep=time.sleep):
    state=controller.snapshot()
    # A handover can be between releasing its Viewer and publishing its new
    # run/bridge. Ordinary disconnect is not an acknowledgement of that pending
    # transition. Keep the window open; retry works once its owner has finished.
    if state.get('calibration_connection',{}).get('busy'):
        return dict(ok=False,error='标定任务正在切换，请等待完成或在原任务中取消后重试 / Calibration handover is still in progress; wait or cancel it in its owning task before retrying')
    if state.get('teleoperation_connection',{}).get('busy'):
        return dict(ok=False,error='遥操作任务正在切换，请等待完成或在原任务中取消后重试 / Teleoperation handover is still in progress; wait or cancel it in its owning task before retrying')
    continuation=getattr(controller,'calibrated_teleoperation',None)
    if state.get('calibrated_teleoperation',{}).get('busy'):
        if continuation is None or not continuation.cancel_and_wait(timeout):
            return dict(ok=False,error='标定资料接续仍在结束，请稍后重试 / Calibration continuation is still closing; retry shortly')
        state=controller.snapshot()
    if hasattr(controller,'official_cli'):controller.official_cli.closed.set()
    if state.get('official_viz',{}).get('active') or state.get('official_viz',{}).get('busy') or state.get('official_viz',{}).get('reserved'):
        try:controller.official_viz.stop()
        except ValueError as error:return dict(ok=False,error=str(error))
    studio=state.get('official_studio',{})
    if studio.get('busy') and not studio.get('active'):
        return dict(ok=False,error='请等待 Studio 组件操作完成 / Wait for the Studio component operation')
    if studio.get('active') or studio.get('reserved'):
        try:controller.studio.stop()
        except ValueError as error:return dict(ok=False,error=str(error))
    if state.get('parameter_sync',{}).get('busy') or controller.doctor.snapshot().get('running'):
        return dict(ok=False,error='请等待参数同步或诊断结束。 / Wait for parameter sync or diagnostics.')
    if state.get('calibration',{}).get('running'):
        controller.action(dict(name='calibration_cancel'))
        deadline=clock()+timeout
        while controller.calibration.run.get('running') and clock()<deadline:sleep(.05)
        if controller.calibration.run.get('running'):
            return dict(ok=False,error='官方标定仍在退出，请稍后重试 / Official calibration is still releasing the device')
    glove=state.get('glove',{})
    hardware=glove.get('hardware',{}) if glove.get('busy') else state.get('hardware',{})
    active=hardware.get('active',False)
    current_device=(glove.get('feedback') or {}).get('device_id') if glove.get('busy') else state.get('device_id')
    pending_here=bool(current_device) and any(r.get('device_id')==current_device for r in unconfirmed(state))
    if active is not False or pending_here:
        try:controller.action(dict(name='hardware_stop'))
        except ValueError as error:return dict(ok=False,error=str(error))
        deadline=clock()+timeout
        while clock()<deadline:
            state=controller.snapshot()
            hw=state.get('glove',{}).get('hardware',{}) if glove.get('busy') else state.get('hardware',{})
            if hw.get('active') is False and hw.get('stop_confirmed') is True:break
            sleep(.05)
        else:return dict(ok=False,error='尚未确认灵巧手停用，请回到工作台检查连接。 / Hand disable is not confirmed; check the connection.')
    pending=unconfirmed(state)
    if pending:
        devices=', '.join(str(r.get('device_id') or '未知设备 / Unknown device') for r in pending)
        return dict(ok=False,error='仍有设备未确认停用：'+devices+' / Device disable acknowledgement is still missing')
    if glove.get('busy'):
        controller.action(dict(name='glove_disconnect'))
        deadline=clock()+timeout
        while controller.glove.busy and clock()<deadline:sleep(.05)
        if controller.glove.busy:return dict(ok=False,error='手套会话仍在结束，请稍后重试。 / Glove session is still closing; retry shortly.')
        # Cleanup can itself fail after the earlier idle snapshot. Validate
        # its final acknowledgement before closing the application.
        state=controller.snapshot()
        if unconfirmed(state):
            return dict(ok=False,error='手套退出后仍未确认灵巧手停用，请核对对应设备 / Hand disable remains unconfirmed after glove cleanup; check the matching device')
    controller.player.command(dict(name='demo_stop'))
    controller.action(dict(name='disconnect'))
    # Existing SDK disconnect gives recording flush and disable a grace period.
    if state.get('connection')!='disconnected':sleep(3.1)
    if hasattr(controller,'official_cli') and not controller.official_cli.cancel_and_wait():
        return dict(ok=False,error='CLI 下载正在取消，当前设备会话已结束，请稍后重试 / CLI download is cancelling; device sessions have ended, retry shortly')
    return dict(ok=True)
