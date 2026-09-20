"""Bounded draining of the SDK's documented non-blocking receive API."""
import time


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
