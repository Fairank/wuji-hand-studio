"""One root-owned firmware workflow, using the official CLI's own confirmation.

The pinned CLI's captured --check JSON and documented table are read-only facts.
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


class ReadOnlyQueryUnconfirmed(ValueError):
    """The host retained a read-only child whose actual exit is not proven."""


class FirmwareNotStarted(ValueError):
    """The host proved no mutating child was started for this request."""


class FirmwareWriterUnconfirmed(ValueError):
    """A possible existing writer must keep its original reservation."""


class FirmwareReservationHeld(ValueError):
    """An acquire failure retained this exact never-started reservation."""
    def __init__(self, token):
        super().__init__('固件准备占用释放未确认 / Firmware preparation release is unconfirmed')
        self.token = token


def _text(value, limit=1024):
    return value if isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value) else None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _serial(value):
    if not isinstance(value, str) or not _SERIAL.fullmatch(value):
        raise ValueError('请选择一个已发现的设备 / Select one discovered device')
    return value


def official_failure_detail(output,sn):
    """Display one exact-SN failed row; never infer completion or progress.

    Raw terminal output remains in the result and terminal archive. Unknown
    table formats and other devices cannot become this device's explanation.
    """
    if not isinstance(output,str) or len(output)>_MAX_REPORT:return None
    clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',output)
    clean=re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)','',clean)
    found=[];inside=False;current=None;target_rows=0
    for line in clean.splitlines():
        if line.startswith('└'):
            if not re.fullmatch(r'└─+(?:┴─+){5}┘',line):return None
            if current:found.append(current)
            current=None;inside=False
            continue
        if not line.startswith('│') or not line.endswith('│'):continue
        cells=[part.strip() for part in line[1:-1].split('┆')]
        if [cell.lower() for cell in cells]==['sn','status','before','after','duration','detail']:
            if inside:return None
            if current:found.append(current)
            current=None;inside=True
            continue
        if not inside or len(cells)!=6:continue
        if cells[0]==sn:target_rows+=1
        if all(not cell for cell in cells[:5]) and current is not None:
            current+=' '+cells[5]
            continue
        if current:found.append(current)
        current=cells[5] if cells[0]==sn and cells[1].lower()=='failed' else None
    # A clipped table (or repeated/conflicting summaries) is retained as raw
    # output, never guessed into a short explanation.
    return found[0] if not inside and target_rows==1 and len(found)==1 and _text(found[0],700) else None


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
                 preview_ttl=120, freshness_ttl=30, cancel_query=None,
                 claim_package=None, validate_package=None, file_readback=None):
        if not all(callable(value) for value in (executor, cli_identity, idle_check, acquire, release, device_provider, clock)):
            raise TypeError('Firmware host callbacks are required')
        if not _finite(preview_ttl) or not 1 <= preview_ttl <= 600:
            raise ValueError('Invalid firmware preview lifetime')
        if not _finite(freshness_ttl) or not 1 <= freshness_ttl <= 120:
            raise ValueError('Invalid device freshness lifetime')
        if recovery_check is not None and not callable(recovery_check):
            raise TypeError('Invalid firmware recovery callback')
        if cancel_query is not None and not callable(cancel_query):
            raise TypeError('Invalid read-only cancellation callback')
        if any(value is not None for value in (claim_package, validate_package, file_readback)) and not all(
                callable(value) for value in (claim_package, validate_package, file_readback)):
            raise TypeError('All official package callbacks are required')
        self.executor, self.cli_identity = executor, cli_identity
        self.idle_check, self.acquire, self.release = idle_check, acquire, release
        self.device_provider, self.recovery_check = device_provider, recovery_check
        self.cancel_query = cancel_query
        self.claim_package, self.validate_package, self.file_readback = claim_package, validate_package, file_readback
        self.clock, self.preview_ttl, self.freshness_ttl = clock, float(preview_ttl), float(freshness_ttl)
        self.lock = threading.RLock()
        self.thread = None
        self._reservation = None
        self._preview = None
        self._cancelled = False
        self.state = dict(busy=False, locked=False, phase='idle', device=None,
                          preview=None, check_report=None, catalog_report=None,
                          result=None, error=None, events=[], checked_at=None, verification_report=None,
                          finished_at=None, recovery_required=False,
                          notification=None, current_version=None, target_version=None,
                          check_observation=None, catalog_error=None,
                          operation=None, automatic=False,
                          operation_generation=0,
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
            self.state.update(busy=True, phase=phase, error=None, result=None, verification_report=None,
                              events=[], finished_at=None, recovery_required=False,
                              operation_generation=self.state.get('operation_generation', 0)+1)

    def _reserve(self, kind='firmware'):
        self.idle_check()
        try:token = self.acquire(kind)
        except FirmwareReservationHeld as error:
            if type(error.token) is str and 0 < len(error.token) <= 256:
                with self.lock:self._reservation = error.token
            raise
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
            self._finish('check_failed' if self.state['operation']=='check' else 'failed', '无法启动固件任务 / Cannot start firmware task')
            raise

    def start_check(self, sn, *, notification_only=False):
        # API accepts only a serial. Client device metadata is never trusted.
        _serial(sn)
        self._begin('checking')
        with self.lock:
            self._preview = None
            self._cancelled = False
            self.thread = None
            self.state.update(preview=None, check_report=None, catalog_report=None, device=None,
                              notification=None,checked_at=None,current_version=None,target_version=None,
                              check_observation=None,catalog_error=None,
                              operation='check',automatic=notification_only,mode='official_confirmation')
        try:
            device, cli = self._device(sn), _identity(self.cli_identity())
            # Acquire can retain a never-started read reservation if its final
            # idle check and release both fail. Recovery still needs the exact
            # server-observed device in that case.
            with self.lock:self.state['device'] = copy.deepcopy(device)
            self._reserve('firmware_check')
            self._launch(self._check, device, cli, notification_only)
        except Exception as error:
            self._finish('check_failed', str(error)[:800])
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

    def _query(self, argv, *, automatic=False):
        try:
            value = self.executor(argv, mutating=False, interactive=False,
                                  on_event=self._event, background=automatic,
                                  cancelled=(lambda:self._cancelled) if automatic else None)
        except ReadOnlyQueryUnconfirmed:
            raise
        except Exception as error:
            raise _UnconfirmedProcess('官方检查进程结束状态未确认 / Official query process exit is unconfirmed') from error
        code, out, err = self._response(value)
        # Preserve the official original rather than interpreting an unverified
        # envelope/status enum into an application-generated firmware plan.
        return dict(stdout=out, stderr=err, returncode=code)

    def _check(self, device, cli, notification_only=False):
        try:
            with self.lock:
                if notification_only and self._cancelled:raise ValueError('Background check deferred')
            sn = device['sn']
            from device_versions import parse_official_check_report, observe_official_check_failure
            check = self._query(['upgrade', '--sn', sn, '--check', '--json'], automatic=notification_only)
            with self.lock:
                self.state['check_report'] = check
            if _identity(self.cli_identity()) != cli:
                raise ValueError('官方 CLI 已变化，请重新检查 / Official CLI changed; check again')
            expected_type='wuji_glove' if device.get('kind')=='glove' else 'wuji_hand_2' if device.get('generation')=='hand2' else 'wuji_hand' if device.get('generation')=='hand1' else None
            failed = observe_official_check_failure(check['stdout'],sn,expected_type) if expected_type else None
            if failed is not None:
                with self.lock:
                    self.state.update(check_observation=failed,catalog_error=failed['error'],
                        current_version=failed['current'],target_version=None,checked_at=self._now(),notification=None)
                self._finish('check_failed',failed['error'][:800])
                return
            if check['returncode']:
                raise ValueError((check['stderr'] or check['stdout'] or '暂时无法检查设备更新 / Device update check is unavailable')[:800])
            report = parse_official_check_report(check['stdout'],sn,expected_type) if expected_type else None
            now, token = self._now(), secrets.token_urlsafe(24)
            public = dict(token=token, sn=sn, device=copy.deepcopy(device),
                          mode='official_confirmation', target_version=None,
                          expires_at=now + self.preview_ttl,
                          requires_official_confirmation=True)
            with self.lock:
                if not notification_only and (report is None or report['update_available']):
                    self._preview = dict(token=token, device=copy.deepcopy(device), cli=cli,
                                         checked_device=copy.deepcopy(report),
                                         created_at=now, expires_at=now + self.preview_ttl,
                                         public=public)
                self.state.update(notification=report, checked_at=now,
                                  current_version=report['current'] if report else None,
                                  target_version=report['latest'] if report else None)
            self._finish('available' if report and report['update_available'] else 'up_to_date' if report else 'checked' if notification_only else 'ready')
        except Exception as error:
            with self.lock:
                self._preview = None
            # The host's finite read-only executor owns timeout cleanup and must
            # confirm its query process has ended before returning/raising. A
            # read never enters the non-cancellable flash lifecycle.
            if isinstance(error,ReadOnlyQueryUnconfirmed):
                self._finish('unknown',str(error)[:800],unknown=True)
            elif notification_only and self._cancelled:
                self._finish('deferred')
            else:self._finish('check_failed', str(error)[:800])

    def cancel_notification_check(self, timeout=3):
        """Give explicit user actions priority over a finite background read.

        The host callback only cancels the separate --check subprocess. No
        interactive firmware child or manual operation is ever cancelled here.
        A true return means the read thread and exact reservation ended.
        """
        with self.lock:
            if self.state['operation']=='check' and self.state['automatic'] and self.state['locked']:
                return False
            if not self.state['busy'] or self.state['operation']!='check' or not self.state['automatic']:
                return True
            self._cancelled = True
            thread = self.thread
        if self.cancel_query is not None:self.cancel_query()
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self.lock:
                if not self.state['busy']:
                    return not self.state['locked'] and self._reservation is None
                thread=self.thread
            if thread is threading.current_thread():return False
            if thread is not None:thread.join(min(.05,max(0,deadline-time.monotonic())))
            else:time.sleep(.01)
        return False

    def _preview_device(self, preview):
        # A successful exact-SN official check remains confirmation authority
        # for this bounded preview lifetime, even when discovery advertisements
        # expire after 30 s. This is not a target firmware/revision write plan:
        # the unchanged CLI reconnects the exact SN and asks its own confirmation.
        if preview.get('checked_device') is not None:
            if not preview['created_at'] <= self._now() < preview['expires_at']:
                raise ValueError('固件预览已过期，请重新检查 / Firmware preview expired; check again')
            return copy.deepcopy(preview['device'])
        return self._device(preview['device']['sn'])

    def start_file_upgrade(self, sn, package_token, *, confirmed=False):
        """Open the official ZIP flow; its confirmation is never auto-answered."""
        _serial(sn)
        if confirmed is not True or self.claim_package is None:
            raise ValueError('请明确打开官方安装流程 / Explicitly open the official installation')
        if not isinstance(package_token, str) or not 1 <= len(package_token) <= 256:
            raise ValueError('Invalid official package token')
        self._begin('preparing')
        with self.lock:
            self._preview = None
            self.state.update(operation='upgrade', automatic=False, mode='official_file_confirmation',
                device=None, preview=None, notification=None, check_report=None, catalog_report=None,
                check_observation=None, catalog_error=None, current_version=None, target_version=None, checked_at=None)
        try:
            device, cli = self._device(sn), _identity(self.cli_identity())
            if device.get('kind') != 'glove':raise ValueError('Official package mode is available for Glove 1 only')
            with self.lock:self.state['device'] = copy.deepcopy(device)
            self._reserve('firmware')
            archive = self.claim_package(package_token, sn, reservation_token=self._reservation)
            fields = {'claim_id', 'reservation_token', 'serial', 'device_type', 'version', 'sha256', 'size', 'path', 'entry_id'}
            if (type(archive) is not dict or set(archive) != fields or archive['serial'] != sn
                    or archive['reservation_token'] != self._reservation
                    or archive['device_type'] != 'wuji_glove' or not _text(archive['claim_id'], 256)
                    or type(archive['version']) is not str or re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}', archive['version']) is None
                    or type(archive['sha256']) is not str or re.fullmatch('[a-f0-9]{64}', archive['sha256']) is None
                    or type(archive['size']) is not int or not 0 < archive['size'] <= 512*1024*1024
                    or not _text(archive['path'], 4096) or not _text(archive['entry_id'], 80)):
                raise ValueError('Invalid server-owned official package')
            if self.validate_package(archive) != archive:raise ValueError('Official package changed')
            preview = dict(device=copy.deepcopy(device), cli=cli, archive=copy.deepcopy(archive), checked_device=None)
            with self.lock:
                self.state.update(device=device, target_version=archive['version'],
                    catalog_report=dict(source='official_package', version=archive['version'], sha256=archive['sha256']))
            self._launch(self._upgrade, device, preview)
        except Exception as error:
            uncertain = isinstance(error, FirmwareWriterUnconfirmed)
            self._finish('unknown' if uncertain else 'failed', str(error)[:800], unknown=uncertain)
            raise
        return self.snapshot()

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
            self.state.update(operation='upgrade',automatic=False,mode='official_confirmation')
            # One use even when idle check subsequently refuses the action.
            self._preview = None
        try:
            device = self._preview_device(preview)
            if device != preview['device'] or _identity(self.cli_identity()) != preview['cli']:
                raise ValueError('设备或官方 CLI 已变化，请重新检查 / Device or official CLI changed; check again')
            self._reserve()
            # No --yes: the human sees and answers the actual CLI plan. No --all,
            # --type, raw file, --force, downgrade, rollback or implicit retries.
            self._launch(self._upgrade, device, preview)
        except Exception as error:
            self._finish('failed', str(error)[:800])
            raise
        return self.snapshot()

    def _upgrade(self, device, preview):
        attempted = False
        try:
            # The exact CLI and server inventory are rechecked immediately before
            # launch, after obtaining the shared reservation.
            if self._preview_device(preview) != device or _identity(self.cli_identity()) != preview['cli']:
                self._finish('failed', '设备或官方 CLI 已变化 / Device or official CLI changed')
                return
            archive = preview.get('archive')
            if archive is not None and self.validate_package(archive) != archive:
                raise ValueError('Official package changed before launch')
            argv = ['upgrade', '--sn', device['sn']]
            if archive is not None:argv += ['--file', archive['path']]
            attempted = True
            value = self.executor(argv, mutating=True,
                                  interactive=True, on_event=self._event)
            code, out, err = self._response(value)
            with self.lock:
                self.state['result'] = dict(returncode=code, stdout=out, stderr=err,
                                           sn=device['sn'],verdict='official_process_exited')
            # Human-mode exit zero includes No/cancel/no-change. Read the same
            # device while retaining the lease, and bind success to the target
            # from this operation's verified preflight, never to terminal text.
            from device_versions import observe_official_check_failure
            expected_type='wuji_glove' if device.get('kind')=='glove' else 'wuji_hand_2' if device.get('generation')=='hand2' else 'wuji_hand' if device.get('generation')=='hand1' else None
            failed=observe_official_check_failure(out,device['sn'],expected_type) if expected_type else None
            if code or failed is not None:
                detail=official_failure_detail(out,device['sn'])
                if failed is not None:
                    # The exited process result remains history, but facts
                    # from a changed/unverified component are not published.
                    try:unchanged=_identity(self.cli_identity())==preview['cli']
                    except Exception:unchanged=False
                    if not unchanged:
                        self._finish('failed','官方 CLI 已变化，当前版本尚未核对 / Official CLI changed; the current version is unverified')
                        return
                    detail=failed['error']
                    with self.lock:
                        self.state.update(check_observation=failed,catalog_error=detail,
                            current_version=failed['current'],target_version=None,notification=None,checked_at=self._now())
                with self.lock:self.state['result']['official_error']=detail
                self._finish('failed', (err or detail or '官方升级未完成 / Official upgrade did not complete')[:800])
                return
            if archive is not None:self._verify_file_upgrade(device, preview)
            else:self._verify_upgrade(device, preview)
        except Exception as error:
            if isinstance(error, FirmwareNotStarted):attempted = False
            if isinstance(error, FirmwareWriterUnconfirmed):attempted = True
            self._finish('unknown' if attempted else 'failed',
                         '固件升级结束状态未确认，请核验设备 / Firmware completion is unconfirmed; verify the device' if attempted else str(error)[:800],
                         unknown=attempted)

    def _verify_file_upgrade(self, device, preview):
        """Read identity/version through official resources, not the catalog."""
        with self.lock:self.state.update(phase='verifying', notification=None)
        try:
            if _identity(self.cli_identity()) != preview['cli']:raise ValueError('Official CLI changed')
            observed = self.file_readback(device['sn'], reservation_token=self._reservation)
            if _identity(self.cli_identity()) != preview['cli']:raise ValueError('Official CLI changed')
            if (type(observed) is not dict or observed.get('process_exited') is not True
                    or observed.get('identity_verified') is not True or observed.get('sn') != device['sn']
                    or observed.get('reservation_token') != self._reservation
                    or type(observed.get('firmware_version')) is not str
                    or re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}', observed['firmware_version']) is None):
                raise ValueError('Official device version readback is unverified')
            current, target = observed['firmware_version'], preview['archive']['version']
            matched = current == target
            with self.lock:
                public_report = {key:copy.deepcopy(observed[key]) for key in ('source', 'sn', 'firmware_version', 'resources') if key in observed}
                self.state.update(current_version=current, checked_at=self._now(), verification_report=public_report)
                self.state['result'].update(sn=device['sn'], firmware_version=current, expected_version=target,
                    version_verified=matched, source='official_resource_readback',
                    verdict='target_version_verified' if matched else 'version_not_matched')
            self._finish('verified' if matched else 'completed', None if matched else
                '设备版本已读回，但未匹配本次目标 / Device version was read but does not match this target')
        except ReadOnlyQueryUnconfirmed:
            raise
        except Exception as error:
            self._finish('completed', '官方安装流程已结束，设备版本尚未核对 / Official installation ended; device version is unverified: '+str(error)[:400])

    def _verify_upgrade(self, device, preview):
        from device_versions import parse_official_check_report, observe_official_check_failure
        before=preview.get('checked_device')
        expected_type='wuji_glove' if device.get('kind')=='glove' else 'wuji_hand_2' if device.get('generation')=='hand2' else 'wuji_hand' if device.get('generation')=='hand1' else None
        with self.lock:
            self.state.update(phase='verifying',notification=None,current_version=None,checked_at=None,
                              check_observation=None,catalog_error=None)
        if _identity(self.cli_identity())!=preview['cli']:
            self._finish('completed','官方流程已结束，但组件已变化，设备版本尚未确认 / The official flow ended, but the component changed; verify the device version')
            return
        check=self._query(['upgrade','--sn',device['sn'],'--check','--json'])
        with self.lock:self.state['verification_report']=check
        report=(parse_official_check_report(check['stdout'],device['sn'],expected_type)
                if check['returncode']==0 and expected_type else None)
        failed=observe_official_check_failure(check['stdout'],device['sn'],expected_type) if expected_type else None
        if _identity(self.cli_identity())!=preview['cli']:report=None;failed=None
        if failed is not None:
            with self.lock:
                self.state.update(check_observation=failed,catalog_error=failed['error'],
                    current_version=failed['current'],target_version=None,checked_at=self._now())
                self.state['result'].update(sn=device['sn'],firmware_version=failed['current'],
                    expected_version=before['latest'] if before else None,
                    version_verified=False,verdict='catalog_unavailable')
            self._finish('completed','官方流程已结束，但兼容固件列表尚未确认，请重新检查 / The official flow ended; compatible firmware could not be checked. '+failed['error'][:500])
            return
        if report is None:
            detail=(check['stderr'] or '设备身份与版本未通过核对 / Device identity and version were not verified')[:500]
            self._finish('completed','官方流程已结束，但未能读回设备版本，请重新检查 / The official flow ended; check the device version again. '+detail)
            return
        matched=bool(before and before.get('update_available') is True
                     and before['serial']==report['serial'] and before['device_type']==report['device_type']
                     and report['current']==before['latest'])
        unchanged=bool(before and report['current']==before['current'])
        with self.lock:
            self.state.update(notification=report,current_version=report['current'],checked_at=self._now())
            self.state['result'].update(sn=device['sn'],firmware_version=report['current'],
                expected_version=before['latest'] if before else None,version_verified=matched,
                verdict='target_version_verified' if matched else 'unchanged' if unchanged else 'version_not_matched')
        # A changed catalog does not rewrite this operation's target. The fresh
        # report can independently advertise another release without claiming
        # that this run installed it.
        self._finish('verified' if matched else 'completed',None if matched or unchanged else
            '设备版本已读回，但未匹配本次更新目标，请重新检查 / The device version was read but did not match this update target; check again')

    def verify_recovery(self):
        """Explicit host-only recovery; not a client-supplied unlock assertion."""
        with self.lock:
            if not self.state['locked'] or self.state['busy'] or self._reservation is None:
                raise ValueError('没有待核验的固件会话 / No firmware session needs verification')
            if self.recovery_check is None:
                raise ValueError('需要核验官方进程与设备 / Verify the official process and actual device first')
            device = self.state.get('device')
            if not isinstance(device, dict) or not isinstance(device.get('sn'), str):
                raise ValueError('原设备标识尚未确认，维护仍保留 / Original device identity is unavailable; maintenance is retained')
            sn = _serial(device['sn'])
            self.state['busy'] = True
        try:
            with self.lock:
                token = self._reservation
                read_only = self.state['operation']=='check'
            if read_only:
                result=self.recovery_check(sn,reservation_token=token,read_only=True)
                if (not isinstance(result,dict) or result.get('process_exited') is not True
                        or result.get('read_only') is not True or result.get('sn')!=sn
                        or result.get('reservation_token')!=token):
                    raise ValueError('版本检查进程尚未确认退出 / Version-check process exit remains unconfirmed')
                with self.lock:self.state['result']=dict(
                    verdict='readonly_query_not_started' if result.get('no_read_started') is True else 'readonly_query_exited',sn=sn)
                self._finish('checked')
                return self.snapshot()
            result = self.recovery_check(sn, reservation_token=token)
            if (type(result) is dict and set(result) == {'no_write_started', 'process_exited', 'sn', 'reservation_token'}
                    and result['no_write_started'] is True and result['process_exited'] is True
                    and result['sn'] == sn and result['reservation_token'] == token):
                with self.lock:self.state['result'] = dict(verdict='official_write_not_started', sn=sn)
                self._finish('completed', '官方安装尚未启动，可以重新准备 / Official installation did not start; prepare again')
                return self.snapshot()
            if (not isinstance(result, dict) or result.get('process_exited') is not True or
                    result.get('identity_verified') is not True or result.get('sn') != sn or
                    result.get('reservation_token') != token or
                    not _text(result.get('firmware_version'), 80) or not result['firmware_version']):
                raise ValueError('官方进程或设备版本尚未确认 / Official process or actual device version remains unconfirmed')
            verification=result.get('verification_report');observed=None
            if verification is not None:
                from device_versions import parse_official_check_json
                code,out,err=self._response(verification)
                device=self.state['device']
                expected_type=('wuji_glove' if device.get('kind')=='glove' else
                    'wuji_hand_2' if device.get('generation')=='hand2' else
                    'wuji_hand' if device.get('generation')=='hand1' else None)
                observed=parse_official_check_json(out,sn,expected_type) if code==0 else None
                if observed is None or observed['current']!=result['firmware_version']:
                    raise ValueError('恢复核验的设备身份或版本不一致 / Recovery device identity or version is inconsistent')
            with self.lock:
                self.state['result'] = dict(verdict='recovery_verified', sn=sn,
                                            firmware_version=result['firmware_version'])
                if result.get('source') == 'official_resource_readback':
                    self.state['result']['source'] = 'official_resource_readback'
                    verification = {key:copy.deepcopy(result[key]) for key in ('source', 'sn', 'firmware_version', 'resources') if key in result}
                # A recovered write is a new actual observation. Do not let
                # the preflight version or reminder masquerade as this read.
                self.state.update(current_version=result['firmware_version'],checked_at=self._now(),
                    notification=copy.deepcopy(observed),verification_report=copy.deepcopy(verification))
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
                              operation='upgrade',
                              error='上次固件操作结束状态待核验 / Previous firmware completion needs verification')
            if (marker.get('command') == 'official-interactive-file-upgrade' or marker.get('package_mode') == 'official_file_confirmation'):
                self.state['mode'] = 'official_file_confirmation'
                if re.fullmatch(r'WG1[JK][A-Z0-9]{12}', sn):self.state['device'].update(kind='glove', generation='glove1')
                target = marker.get('package_version')
                if type(target) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}', target):
                    self.state['target_version'] = target
        return self.snapshot()
