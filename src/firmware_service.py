"""One root-owned firmware workflow, using the official CLI's own confirmation.

There is deliberately no guessed firmware-catalog or --check/--list JSON schema.
Read-only reports may be displayed, but only the embedded official interactive
flow can write. Opening that flow never answers its confirmation prompt.

The host injects a fresh inventory provider, strictly verified CLI identity, and
an executor. The executor receives an argv list *without* the executable and
must never terminate a mutating child on timeout, panel close, or output errors.
It returns {exited: bool, returncode: int, stdout: str, stderr: str}. Interactive
output may be streamed with on_event. Only an explicit exited=True establishes
that the child has ended. An exception/unknown exit during a write retains the
reservation until host-side recovery verifies process death and actual device
identity/version. No direct SDK, shell, network, filesystem, or process work lives
in this module. Construct one instance on the root server, not per workspace.
"""
import copy
import json
import math
import re
import secrets
import threading
import time


_SERIAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
_MAX_REPORT = 2 * 1024 * 1024
_MAX_EVENTS = 60


class _UnconfirmedProcess(ValueError):
    pass


def _text(value, limit=1024):
    return value if isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value) else None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _serial(value):
    if not isinstance(value, str) or not _SERIAL.fullmatch(value):
        raise ValueError('请选择一个已发现的设备 / Select one discovered device')
    return value


def _identity(value):
    # Host identity should include strict binary digest, version and controller.
    if isinstance(value, str) and _text(value, 4096) and value:
        return value
    if not isinstance(value, dict) or not value:
        raise ValueError('官方 CLI 尚未验证 / Official CLI is not verified')
    try:
        result = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('官方 CLI 标识无效 / Invalid official CLI identity') from None
    if len(result) > 8192:
        raise ValueError('官方 CLI 标识过长 / Official CLI identity is too large')
    return result


class FirmwareService:
    """Host callback contract.

    device_provider(sn): server-owned fresh inventory row (never a client row).
    cli_identity(): revalidated executable/controller identity, string or object.
    idle_check(): raise unless every workspace/session is confirmed idle.
    acquire('firmware'): atomically gate, check idle, reserve and return a token.
      The shared reservation must survive dead-owner/unknown-child detection.
    release(token): release only the exact reservation, or raise on uncertainty.
    executor(argv, mutating=False, interactive=False, on_event=callback): see above.
      When interactive=True this is the original CLI terminal; the host owns its
      input route and must not synthesize a yes answer. No automatic cancellation.
    recovery_check(sn, reservation_token=token): optional host-only verification
      result with process_exited=True, identity_verified=True, exact sn,
      reservation_token, and firmware_version.
      It must verify the process for this reservation and read actual device data.
    """

    def __init__(self, executor, cli_identity, idle_check, acquire, release,
                 device_provider, *, recovery_check=None, clock=time.time,
                 preview_ttl=120, freshness_ttl=30):
        if not all(callable(value) for value in (executor, cli_identity, idle_check, acquire, release, device_provider, clock)):
            raise TypeError('Firmware host callbacks are required')
        if not _finite(preview_ttl) or not 1 <= preview_ttl <= 600:
            raise ValueError('Invalid firmware preview lifetime')
        if not _finite(freshness_ttl) or not 1 <= freshness_ttl <= 120:
            raise ValueError('Invalid device freshness lifetime')
        if recovery_check is not None and not callable(recovery_check):
            raise TypeError('Invalid firmware recovery callback')
        self.executor, self.cli_identity = executor, cli_identity
        self.idle_check, self.acquire, self.release = idle_check, acquire, release
        self.device_provider, self.recovery_check = device_provider, recovery_check
        self.clock, self.preview_ttl, self.freshness_ttl = clock, float(preview_ttl), float(freshness_ttl)
        self.lock = threading.RLock()
        self.thread = None
        self._reservation = None
        self._preview = None
        self.state = dict(busy=False, locked=False, phase='idle', device=None,
                          preview=None, check_report=None, catalog_report=None,
                          result=None, error=None, events=[], checked_at=None,
                          finished_at=None, recovery_required=False,
                          notification=None,
                          mode='official_confirmation', machine_write_supported=False)

    def _now(self):
        now = self.clock()
        if not _finite(now) or now < 0:
            raise ValueError('无法确认系统时间 / System time cannot be verified')
        return float(now)

    def _device(self, sn):
        sn = _serial(sn)
        row = self.device_provider(sn)
        if not isinstance(row, dict) or (row.get('sn') or row.get('serial')) != sn:
            raise ValueError('所选设备已变化 / Selected device has changed')
        observed = row.get('observed_at', row.get('last_seen'))
        now = self._now()
        if (row.get('stale') is not False or row.get('selectable') is not True or
                row.get('conflict') is True or row.get('in_use') is True or
                not _finite(observed) or not 0 <= now - observed <= self.freshness_ttl):
            raise ValueError('请重新查找设备后再检查固件 / Refresh device discovery before checking firmware')
        if row.get('kind') not in ('hand', 'glove'):
            raise ValueError('无法确认设备类型 / Device type cannot be verified')
        side = row.get('side', row.get('handedness'))
        if side not in (None, 'left', 'right'):
            raise ValueError('设备左右手信息无效 / Invalid device hand side')
        # This fingerprint binds all identity supplied by the trusted host. A
        # missing revision is not guessed from a generic Hand 2 profile; the CLI
        # itself performs the actual revision-compatible catalog selection.
        fields = ('kind', 'generation', 'side', 'handedness', 'model', 'device_type',
                  'hardware_revision', 'hardware_version', 'identity_source',
                  'identity_verified', 'address', 'transport')
        result = dict(sn=sn)
        for key in fields:
            value = row.get(key)
            if value is not None:
                if not isinstance(value, (str, bool, int)) or isinstance(value, str) and not _text(value, 512):
                    raise ValueError('设备标识无效 / Invalid device identity')
                result[key] = value
        return result

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.state)
            preview = self._preview
            if preview is not None:
                try:
                    now = self._now()
                    fresh = preview['created_at'] <= now < preview['expires_at']
                except ValueError:
                    fresh = False
                value['preview'] = copy.deepcopy(preview['public']) if fresh else None
                value['can_upgrade'] = fresh and not value['busy'] and not value['locked']
            else:
                value['can_upgrade'] = False
            return value

    def _begin(self, phase):
        with self.lock:
            if self.state['busy'] or self.state['locked'] or self._reservation is not None:
                raise ValueError('固件操作尚未结束 / Firmware operation has not finished')
            self.state.update(busy=True, phase=phase, error=None, result=None,
                              events=[], finished_at=None, recovery_required=False)

    def _reserve(self, kind='firmware'):
        self.idle_check()
        token = self.acquire(kind)
        if token is None or token == '':
            raise ValueError('无法锁定固件操作 / Cannot reserve firmware operation')
        with self.lock:
            self._reservation = token
        # Recheck after acquiring the shared gate to close TOCTOU with another
        # workspace entering a device session immediately before reservation.
        self.idle_check()

    def _release(self):
        with self.lock:
            token = self._reservation
        if token is not None:
            self.release(token)
            with self.lock:
                if self._reservation == token:
                    self._reservation = None

    def _finish(self, phase, error=None, *, unknown=False):
        if not unknown:
            try:
                self._release()
            except Exception:
                unknown = True
                error = '固件会话结束状态未确认 / Firmware reservation release is unconfirmed'
        with self.lock:
            try:
                finished_at = self._now()
            except ValueError:
                finished_at = None
            self.state.update(busy=False, locked=unknown, phase='unknown' if unknown else phase,
                              error=error, finished_at=finished_at, recovery_required=unknown)

    def _event(self, event):
        # Preserve bounded terminal/progress facts, never invent percent complete
        # or infer writing success from text or a process-progress event.
        if not isinstance(event, dict):
            return
        try:
            encoded = json.dumps(event, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError):
            return
        if len(encoded) > 16384:
            return
        with self.lock:
            self.state['events'] = (self.state['events'] + [copy.deepcopy(event)])[-_MAX_EVENTS:]

    def _launch(self, function, *args):
        try:
            thread = threading.Thread(target=function, args=args, name='Official firmware workflow', daemon=True)
            self.thread = thread
            thread.start()
        except Exception:
            self._finish('failed', '无法启动固件任务 / Cannot start firmware task')
            raise

    def start_check(self, sn, *, notification_only=False):
        # API accepts only a serial. Client device metadata is never trusted.
        _serial(sn)
        self._begin('checking')
        with self.lock:
            self._preview = None
            self.state.update(preview=None, check_report=None, catalog_report=None, device=None,notification=None,checked_at=None)
        try:
            device, cli = self._device(sn), _identity(self.cli_identity())
            self._reserve('firmware_check')
            with self.lock:
                self.state['device'] = device
            self._launch(self._check, device, cli, notification_only)
        except Exception as error:
            self._finish('failed', str(error)[:800])
            raise
        return self.snapshot()

    @staticmethod
    def _response(value):
        if not isinstance(value, dict) or value.get('exited') is not True or type(value.get('returncode')) is not int:
            raise _UnconfirmedProcess('官方进程退出状态未确认 / Official process exit is unconfirmed')
        stdout, stderr = value.get('stdout', ''), value.get('stderr', '')
        if not isinstance(stdout, str) or not isinstance(stderr, str) or len(stdout) > _MAX_REPORT or len(stderr) > _MAX_REPORT:
            raise ValueError('官方报告超出限制 / Official report exceeds its limit')
        return value['returncode'], stdout, stderr

    def _query(self, argv):
        try:
            value = self.executor(argv, mutating=False, interactive=False, on_event=self._event)
        except Exception as error:
            raise _UnconfirmedProcess('官方检查进程结束状态未确认 / Official query process exit is unconfirmed') from error
        code, out, err = self._response(value)
        if code:
            raise ValueError((err or '官方固件检查失败 / Official firmware check failed')[:800])
        # Preserve the official original rather than interpreting an unverified
        # envelope/status enum into an application-generated firmware plan.
        return dict(stdout=out, stderr=err, returncode=code)

    def _check(self, device, cli, notification_only=False):
        try:
            sn = device['sn']
            if notification_only:
                from device_versions import parse_official_check_table
                check=self._query(['upgrade','--sn',sn,'--check'])
                if _identity(self.cli_identity())!=cli:raise ValueError('Official CLI changed during version check')
                expected_type='wuji_glove' if device.get('kind')=='glove' else 'wuji_hand_2' if device.get('generation')=='hand2' else 'wuji_hand' if device.get('generation')=='hand1' else None
                report=parse_official_check_table(check['stdout'],sn,expected_type) if expected_type else None
                with self.lock:self.state.update(check_report=check,notification=report,checked_at=self._now())
                self._finish('available' if report and report['update_available'] else 'up_to_date' if report else 'checked')
                return
            check = self._query(['upgrade', '--sn', sn, '--check', '--json'])
            with self.lock:
                self.state['check_report'] = check
            catalog = self._query(['upgrade', '--sn', sn, '--list', '--json'])
            # Discovery pauses while we own the reservation. Catalog queries may
            # outlast the 30-second advertisement lifetime; do not pretend a
            # cached observation was refreshed by them. Opening the interactive
            # flow later requires a genuinely fresh server observation again.
            if _identity(self.cli_identity()) != cli:
                raise ValueError('官方 CLI 已变化，请重新检查 / Official CLI changed; check again')
            now, token = self._now(), secrets.token_urlsafe(24)
            public = dict(token=token, sn=sn, device=copy.deepcopy(device),
                          mode='official_confirmation', target_version=None,
                          expires_at=now + self.preview_ttl,
                          requires_official_confirmation=True)
            with self.lock:
                self._preview = dict(token=token, device=copy.deepcopy(device), cli=cli,
                                     created_at=now, expires_at=now + self.preview_ttl,
                                     public=public)
                self.state.update(catalog_report=catalog, checked_at=now)
            self._finish('ready')
        except Exception as error:
            with self.lock:
                self._preview = None
            # The host's finite read-only executor owns timeout cleanup and must
            # confirm its query process has ended before returning/raising. A
            # read never enters the non-cancellable flash lifecycle.
            self._finish('failed', str(error)[:800])

    def start_upgrade(self, preview_token, *, confirmed=False):
        if confirmed is not True:
            raise ValueError('请主动打开官方升级流程 / Explicitly open the official upgrade flow')
        with self.lock:
            preview = self._preview
            if not isinstance(preview_token, str) or preview is None or not secrets.compare_digest(preview_token, preview['token']):
                raise ValueError('固件预览已失效，请重新检查 / Firmware preview expired; check again')
            now = self._now()
            if not preview['created_at'] <= now < preview['expires_at']:
                self._preview = None
                raise ValueError('固件预览已过期，请重新检查 / Firmware preview expired; check again')
            self._begin('upgrading')
            # One use even when idle check subsequently refuses the action.
            self._preview = None
        try:
            device = self._device(preview['device']['sn'])
            if device != preview['device'] or _identity(self.cli_identity()) != preview['cli']:
                raise ValueError('设备或官方 CLI 已变化，请重新检查 / Device or official CLI changed; check again')
            self._reserve()
            # No --yes: the human sees and answers the actual CLI plan. No --all,
            # --type, raw file, --force, downgrade, rollback or implicit retries.
            self._launch(self._upgrade, device, preview['cli'])
        except Exception as error:
            self._finish('failed', str(error)[:800])
            raise
        return self.snapshot()

    def _upgrade(self, device, cli):
        attempted = False
        try:
            # The exact CLI and server inventory are rechecked immediately before
            # launch, after obtaining the shared reservation.
            if self._device(device['sn']) != device or _identity(self.cli_identity()) != cli:
                self._finish('failed', '设备或官方 CLI 已变化 / Device or official CLI changed')
                return
            attempted = True
            value = self.executor(['upgrade', '--sn', device['sn']], mutating=True,
                                  interactive=True, on_event=self._event)
            code, out, err = self._response(value)
            with self.lock:
                self.state['result'] = dict(returncode=code, stdout=out, stderr=err,
                                           verdict='official_process_exited')
            # Human-mode output is deliberately not scraped into a fabricated
            # per-device verdict. Exit zero includes skipped/cancelled/no-change;
            # this means the official flow ended, not verified device success.
            self._finish('completed' if code == 0 else 'failed',
                         None if code == 0 else (err or '官方升级未完成 / Official upgrade did not complete')[:800])
        except Exception as error:
            self._finish('unknown' if attempted else 'failed',
                         '固件升级结束状态未确认，请核验设备 / Firmware completion is unconfirmed; verify the device' if attempted else str(error)[:800],
                         unknown=attempted)

    def verify_recovery(self):
        """Explicit host-only recovery; not a client-supplied unlock assertion."""
        with self.lock:
            if not self.state['locked'] or self.state['busy'] or self._reservation is None:
                raise ValueError('没有待核验的固件会话 / No firmware session needs verification')
            if self.recovery_check is None:
                raise ValueError('需要核验官方进程与设备 / Verify the official process and actual device first')
            sn = self.state['device']['sn']
            self.state['busy'] = True
        try:
            with self.lock:
                token = self._reservation
            result = self.recovery_check(sn, reservation_token=token)
            if (not isinstance(result, dict) or result.get('process_exited') is not True or
                    result.get('identity_verified') is not True or result.get('sn') != sn or
                    result.get('reservation_token') != token or
                    not _text(result.get('firmware_version'), 80) or not result['firmware_version']):
                raise ValueError('官方进程或设备版本尚未确认 / Official process or actual device version remains unconfirmed')
            with self.lock:
                self.state['result'] = dict(verdict='recovery_verified', sn=sn,
                                            firmware_version=result['firmware_version'])
            self._finish('verified')
        except Exception as error:
            self._finish('unknown', str(error)[:800], unknown=True)
            raise
        return self.snapshot()

    def restore_unknown(self, marker):
        """Restore a persistent firmware lease supplied by trusted root code.

        This does not clear the lease or assert process death. Recovery must use
        the same host-owned marker/child process identity and actual device read.
        Never expose this method with client-provided marker fields.
        """
        if (not isinstance(marker, dict) or marker.get('kind') != 'firmware' or
                not isinstance(marker.get('token'), str) or
                not re.fullmatch(r'[0-9a-f]{32}', marker['token'])):
            raise ValueError('持久固件会话标记无效 / Invalid persistent firmware marker')
        sn = _serial(marker.get('serial'))
        with self.lock:
            if self.state['busy'] or self.state['locked'] or self._reservation is not None:
                raise ValueError('固件会话已存在 / Firmware session already exists')
            self._reservation = marker['token']
            self._preview = None
            self.state.update(busy=False, locked=True, phase='unknown', device=dict(sn=sn),
                              preview=None, result=None, recovery_required=True,
                              error='上次固件操作结束状态待核验 / Previous firmware completion needs verification')
        return self.snapshot()
