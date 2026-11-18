"""Bounded Windows retries for one already-owned atomic filesystem move."""
import os
from pathlib import Path
import stat
import time


_WINDOWS = os.name == 'nt'
_UNKNOWN = object()
_UNCHANGED = 'unchanged'
_COMMITTED = 'committed'
_CONFLICT = 'conflict'


class CommitConflict(OSError):
    """An operand or its parent changed; preserve staging and recovery files."""


def _snapshot(path):
    try:
        info = os.lstat(path)
        kind = stat.S_IFMT(info.st_mode)
        identity = (info.st_dev, info.st_ino, kind) if info.st_ino else None
        attributes = getattr(info, 'st_file_attributes', 0)
        reparse = (stat.S_ISLNK(info.st_mode)
                   or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
                   or bool(getattr(info, 'st_reparse_tag', 0)))
        facts = (info.st_mode, attributes)
        if stat.S_ISREG(info.st_mode):
            facts += (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        return identity, facts, reparse
    except FileNotFoundError:
        return None
    except Exception:
        # Observation failure must not prevent the primitive's first attempt.
        return _UNKNOWN


def _safe(snapshot, *, directory=False):
    return (snapshot is not _UNKNOWN and snapshot is not None
            and snapshot[0] is not None and not snapshot[2]
            and snapshot[0][2] in ((stat.S_IFDIR,) if directory else (stat.S_IFREG, stat.S_IFDIR)))


def path_identity(path):
    """Return no-follow (device, inode, type), or None when unverified."""
    snapshot = _snapshot(path)
    return snapshot[0] if _safe(snapshot) else None


def same_identity(path, identity):
    """Unknown identities never authorize cleanup, even if both are None."""
    return identity is not None and path_identity(path) == identity


def _capture(source, destination):
    try:
        paths = (Path(source), Path(destination))
        return tuple(_snapshot(path) for path in (*paths, paths[0].parent, paths[1].parent))
    except Exception:
        return None


def _assess(before, current):
    if (before is None or current is None or not _safe(before[0])
            or before[1] is not None and not _safe(before[1])
            or not all(_safe(item, directory=True) for item in before[2:])):
        return _UNKNOWN
    uncertain = False
    for old, new in zip(before[2:], current[2:]):
        if new is _UNKNOWN or new is not None and new[0] is None:
            uncertain = True
        elif new != old:
            return _CONFLICT
    # Rename can finish before its caller receives an exception. File metadata
    # may change during a move; stable object identity establishes completion.
    if (not uncertain and current[0] is None and _safe(current[1])
            and current[1][0] == before[0][0]):
        return _COMMITTED
    for old, new in zip(before[:2], current[:2]):
        if new is _UNKNOWN or new is not None and new[0] is None:
            uncertain = True
        elif new != old:
            # A missing source and unreadable destination is an unknown result,
            # rather than proof that some other object replaced the source.
            if new is None and current[1] is _UNKNOWN:
                uncertain = True
            else:
                return _CONFLICT
    return _UNKNOWN if uncertain else _UNCHANGED


def _require_unchanged(before, source, destination, error):
    result = _assess(before, _capture(source, destination))
    if result == _CONFLICT:
        raise CommitConflict('Atomic move identity changed; preserve staging and recovery files') from error
    if result is _UNKNOWN:
        raise error
    return result


def move_with_retry(source, destination, move, *, check_cancel=None):
    """Run move(source, destination), preserving its primitive and first result.

    Only Windows errors 5/32 receive at most nine 10 ms waits. No filesystem
    action is replayed unless both operands and direct parents remain verified.
    A reconciled, already-completed move returns None. Callers retain ownership
    of locks, payload verification, rollback, and cleanup.
    """
    if check_cancel is not None:
        check_cancel()
    if not _WINDOWS:
        return move(source, destination)
    before = _capture(source, destination)
    previous_error = None
    for attempt in range(10):
        if attempt:
            if _require_unchanged(before, source, destination, previous_error) == _COMMITTED:
                return None
            if check_cancel is not None:
                check_cancel()
                if _require_unchanged(before, source, destination, previous_error) == _COMMITTED:
                    return None
        try:
            return move(source, destination)
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32):
                raise
            if _require_unchanged(before, source, destination, error) == _COMMITTED:
                return None
            if attempt == 9:
                raise
            previous_error = error
            time.sleep(.01)
