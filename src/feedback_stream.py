"""Bounded draining of the SDK's documented non-blocking receive API."""
import time


def readonly_feedback_release(proof, hardware, serial, profile):
    """Validate the worker's exact release of feedback that never enabled."""
    return (isinstance(proof,dict) and set(proof)=={'version','kind','serial','profile','enable_attempted','resources_released'}
        and type(proof['version']) is int and proof['version']==1 and proof['kind']=='readonly_feedback'
        and isinstance(serial,str) and bool(serial) and proof['serial']==serial
        and isinstance(profile,str) and bool(profile) and proof['profile']==profile
        and hardware.get('active') is False and hardware.get('enable_attempted') is False
        and proof['enable_attempted'] is False and proof['resources_released'] is True)


def feedback_release_confirmed(record):
    """Exact dead-reader release, without inventing a hardware disable ACK."""
    if not isinstance(record,dict):return False
    thread=record.get('thread');hardware=record.get('hardware') or {}
    if (not all(record.get(key) is True for key in ('worker_closed','reader_finished','transport_closed'))
            or thread is None or thread.is_alive() or hardware.get('active') is not False):return False
    if hardware.get('stop_confirmed') is True:return True
    serial=record.get('serial');identity=record.get('identity') or {};proof=record.get('feedback_cleanup')
    if identity:
        return serial==identity.get('serial') and readonly_feedback_release(proof,hardware,serial,identity.get('profile'))
    # A failed discovery can prove that its exact requested reader never
    # opened a device. It cannot prove any attached device's motor state.
    return (isinstance(proof,dict) and set(proof)=={'version','kind','serial','enable_attempted','resources_released'}
            and type(proof['version']) is int and proof['version']==1 and proof['kind']=='feedback_not_opened'
            and isinstance(serial,str) and bool(serial) and proof['serial']==serial
            and hardware.get('enable_attempted') is False and hardware.get('stop_confirmed') is False
            and proof['enable_attempted'] is False and proof['resources_released'] is True)


def drain_available(subscription, clock=time.monotonic, limit=512, budget_s=.008):
    """Return received frames with dequeue times and whether the queue is empty.

    Dequeue time is not a hardware arrival timestamp. Never advance a controller
    when the bounded drain has not reached the end of an SDK backlog.
    """
    frames=[];started=clock()
    for _ in range(limit):
        frame=subscription.recv()
        now=clock()
        if frame is None:return frames,True
        frames.append((frame,now))
        if now-started>=budget_s:break
    return frames,False
