"""Explicit official catalog/package tasks; never connects or writes a device.

Catalog information and a verified ZIP are separate from device compatibility.
Only a one-use server-owned claim can reach the official interactive file mode.
"""
import copy
import math
import re
import secrets
import threading
import time


_SN = re.compile(r'WG1[JK][A-Z0-9]{12}\Z')
_VERSION = re.compile(r'(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\Z')


def _token(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value) is not None


class FirmwareCatalogService:
    def __init__(self, fetch, prepare, validate, observed, guard, *, clock=time.time, ttl=300, monotonic=time.monotonic):
        if not all(callable(f) for f in (fetch, prepare, validate, observed, guard, clock, monotonic)):
            raise TypeError('Catalog callbacks are required')
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or not 1 <= ttl <= 600:
            raise ValueError('Invalid catalog lifetime')
        self.fetch, self.prepare, self.validate = fetch, prepare, validate
        self.observed, self.guard, self.clock, self.ttl = observed, guard, clock, ttl
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.thread = None
        self._catalog = self._prepared = None
        self._transfer = None
        self._claims = {}
        self._cached = {}
        self._generation = 0
        self.state = dict(busy=False, phase='idle', serial=None, device_type='wuji_glove',
            follows_operation_generation=None,
            checked_at=None, current_version=None, current_observed_at=None, current_source=None,
            catalog_metadata=None, catalog_token=None, catalog_expires_at=None,
            progress=None, package=None, error=None, compatibility_verified=False, can_upgrade=False)

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('System time is unavailable')
        return float(value)

    @staticmethod
    def _serial(sn):
        if type(sn) is not str or not _SN.fullmatch(sn):
            raise ValueError('请选择一代手套 / Select a Wuji Glove 1')
        return sn

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            try:now = self._now()
            except ValueError:now = float('inf')
            if result['catalog_expires_at'] is not None and (now >= result['catalog_expires_at'] or self._catalog is None or now < self._catalog['created_at']):
                result['catalog_token'] = None
            if result['package'] is not None and (self._transfer is not None or now >= result['package']['expires_at'] or self._prepared is None or now < self._prepared['created_at']):
                result['package']['token'] = None
            return result

    def _begin(self, sn, phase):
        self._serial(sn)
        with self.lock:
            if self.state['busy']:
                raise ValueError('目录任务正在进行 / A catalog task is running')
            preceding = self.guard()
            self._generation += 1
            generation = self._generation
            self.state.update(busy=True, phase=phase, serial=sn, error=None, progress=None,
                follows_operation_generation=preceding if type(preceding) is int and preceding >= 0 else None)
            return generation

    def _launch(self, function, *args):
        try:
            thread = threading.Thread(target=function, args=args, name='Official firmware catalog', daemon=True)
            self.thread = thread
            thread.start()
        except Exception:
            with self.lock:self.state.update(busy=False, phase='failed', error='无法启动目录任务 / Could not start catalog task')
            raise

    def start_check(self, sn):
        generation = self._begin(sn, 'checking')
        with self.lock:
            self._catalog = self._prepared = None
            self.state.update(checked_at=None, current_version=None, current_observed_at=None,
                current_source=None, catalog_metadata=None, catalog_token=None, catalog_expires_at=None, package=None)
        self._launch(self._check, sn, generation)
        return self.snapshot()

    def _check(self, sn, generation):
        try:
            now = self._now()
            try:observation = self.observed(sn)
            except Exception:observation = None  # Optional device history must not block an independent catalog lookup.
            current, observed_at, source = None, None, None
            if (type(observation) is dict and observation.get('serial') == sn
                    and observation.get('device_type') == 'wuji_glove'
                    and type(observation.get('version')) is str and _VERSION.fullmatch(observation['version'])
                    and type(observation.get('observed_at')) in (int, float)
                    and math.isfinite(observation['observed_at']) and 0 <= now - observation['observed_at']
                    and type(observation.get('source')) is str):
                current, observed_at, source = observation['version'], observation['observed_at'], observation['source']
            result = self.fetch(sn, 'wuji_glove', current)
            if (type(result) is not dict or result.get('serial') != sn or result.get('device_type') != 'wuji_glove'
                    or result.get('source') != 'official_firmware_catalog' or result.get('phase') not in ('listed', 'empty')
                    or result.get('compatibility_verified') is not False or result.get('can_upgrade') is not False
                    or type(result.get('catalog_metadata')) is not dict):
                raise ValueError('官方目录身份或格式无效 / Invalid official catalog identity or format')
            checked = self._now()
            token = secrets.token_urlsafe(24)
            metadata = result['catalog_metadata']
            entries = metadata.get('entries')
            if type(entries) is not list:raise ValueError('Invalid official catalog entries')
            public = copy.deepcopy(metadata)
            public['entries'] = [{k: copy.deepcopy(row[k]) for k in ('id', 'version', 'file_size', 'sha256') if k in row} for row in entries]
            with self.lock:
                if generation != self._generation:return
                self._catalog = dict(token=token, serial=sn, created_at=checked, expires_at=checked+self.ttl, result=copy.deepcopy(result))
                self.state.update(busy=False, phase=result['phase'], checked_at=checked,
                    current_version=current, current_observed_at=observed_at, current_source=source,
                    catalog_metadata=public, catalog_token=token, catalog_expires_at=checked+self.ttl)
        except Exception as error:
            with self.lock:
                if generation == self._generation:
                    self.state.update(busy=False, phase='failed', error=str(error)[:1200], catalog_token=None)

    def start_prepare(self, sn, token):
        self._serial(sn)
        self.guard()
        with self.lock:
            current = self._catalog
            if (self.state['busy'] or not _token(token) or current is None
                    or not secrets.compare_digest(token, current['token']) or current['serial'] != sn
                    or not current['created_at'] <= self._now() < current['expires_at']):
                raise ValueError('目录已变化或过期，请重新检查 / Catalog changed or expired; check again')
            metadata = current['result']['catalog_metadata']
            entries = [row for row in metadata['entries'] if row['version'] == metadata['latest_listed']]
            if len(entries) != 1:raise ValueError('No unique official package is available')
            generation = self._begin(sn, 'downloading')
            self._prepared = None
            self.state['package'] = None
            entry = copy.deepcopy(entries[0])
        self._launch(self._prepare, sn, entry, generation)
        return self.snapshot()

    def _prepare(self, sn, entry, generation):
        try:
            def progress(event):
                if type(event) is not dict:raise ValueError('Invalid firmware download event')
                downloaded, total = event.get('received_bytes'), event.get('total_bytes')
                if (type(downloaded) is not int or type(total) is not int
                        or not 0 <= downloaded <= total or total != entry['file_size']):
                    raise ValueError('Invalid firmware download progress')
                with self.lock:
                    if generation == self._generation:self.state['progress'] = dict(downloaded=downloaded, total=total, stage=event.get('stage'))
            key = (sn, entry['id'], entry['sha256'])
            with self.lock:cached = self._cached.get(key)
            package = None
            if cached is not None:
                try:package = self.validate(entry, sn, 'wuji_glove', cached.path)
                except (OSError, ValueError):pass  # Preserve it; an explicit retry uses a fresh attempt folder.
            if package is None:package = self.prepare(entry, sn, 'wuji_glove', on_progress=progress)
            package = self.validate(entry, sn, 'wuji_glove', package.path)
            prepared_at = self._now()
            token = secrets.token_urlsafe(24)
            with self.lock:
                if generation != self._generation:return
                self._prepared = dict(token=token, serial=sn, created_at=prepared_at, expires_at=prepared_at+self.ttl, entry=entry, package=package)
                self._cached[key] = package
                while len(self._cached) > 64:self._cached.pop(next(iter(self._cached)))
                self.state.update(busy=False, phase='package_ready', error=None,
                    package=dict(token=token, version=package.version, sha256=package.archive_sha256,
                        size=package.archive_size, expires_at=prepared_at+self.ttl),
                    progress=dict(downloaded=package.archive_size, total=package.archive_size))
        except Exception as error:
            with self.lock:
                if generation == self._generation:
                    self.state.update(busy=False, phase='failed', error=str(error)[:1200], package=None)

    @staticmethod
    def _descriptor(package, claim_id, reservation_token):
        return dict(claim_id=claim_id, reservation_token=reservation_token, serial=package.serial, device_type=package.device_type,
            version=package.version, sha256=package.archive_sha256, size=package.archive_size,
            path=str(package.path), entry_id=package.entry_id)

    def claim_package(self, token, sn, *, reservation_token):
        """One-use token, called only inside the firmware writer's fresh gate."""
        self._serial(sn)
        if type(reservation_token) is not str or not 1 <= len(reservation_token) <= 256:
            raise ValueError('Invalid firmware reservation token')
        with self.lock:
            transfer = self._transfer
            if transfer is not None:
                item = self._transfer_item(token, sn)
            else:
                item = self._ready_item(token, sn)
            if len(self._claims) >= 32:raise ValueError('Too many retained firmware package claims; restart after all sessions end')
            package = self.validate(item['entry'], sn, 'wuji_glove', item['package'].path)
            if package != item['package']:raise ValueError('Official package changed')
            if transfer is not None:self._transfer_item(token, sn)
            claim_id = secrets.token_urlsafe(32)
            self._claims[claim_id] = dict(entry=copy.deepcopy(item['entry']), package=package, reservation_token=reservation_token)
            self._prepared = None
            if transfer is not None:transfer['claimed'] = True
            self.state['package'] = None
            self.state['phase'] = 'listed'
            return self._descriptor(package, claim_id, reservation_token)

    def validate_ready_token(self, token, sn):
        """Reject invalid UI capabilities before any device discovery occurs."""
        self._serial(sn)
        with self.lock:
            item = self._ready_item(token, sn)
            if self.validate(item['entry'], sn, 'wuji_glove', item['package'].path) != item['package']:
                raise ValueError('Official package changed before device preparation')

    def _ready_item(self, token, sn):
        item = self._prepared
        if (self.state['busy'] or not _token(token) or item is None or item['serial'] != sn
                or not secrets.compare_digest(token, item['token'])
                or not item['created_at'] <= self._now() < item['expires_at']):
            raise ValueError('官方固件包已变化或过期 / Official package changed or expired')
        return item

    def _transfer_now(self):
        value = self.monotonic()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Transfer clock is unavailable')
        return float(value)

    def _transfer_item(self, token, sn):
        transfer = self._transfer
        if (transfer is None or not _token(token) or transfer['claimed']
                or not secrets.compare_digest(token, transfer['token']) or transfer['item']['serial'] != sn
                or not transfer['started_at'] <= self._transfer_now() < transfer['expires_at']):
            raise ValueError('固件转接已变化或超时，请重新准备 / Firmware transfer changed or timed out; prepare again')
        return transfer['item']

    def begin_transfer(self, token, sn):
        """Pin one fresh, exact archive before stopping its Viewer, for 90 s.

        The private capability cannot refresh a UI token or authorize a device
        write. Fresh identity, the normal lease and official confirmation remain
        mandatory. Catalog/download actions cannot replace it during handover.
        """
        self._serial(sn)
        with self.lock:
            self.validate_ready_token(token, sn)
            # Final check under the publication lock: close intent or maintenance
            # may have appeared while the archive was being validated.
            item = self._ready_item(token, sn)
            self.guard()
            started = self._transfer_now()
            private = secrets.token_urlsafe(32)
            self._transfer = dict(token=private, item=copy.deepcopy(item), started_at=started,
                expires_at=started+90., claimed=False)
            self.state['busy'] = True
            return private

    def validate_transfer(self, token, sn):
        self._serial(sn)
        with self.lock:
            item = self._transfer_item(token, sn)
            if self.validate(item['entry'], sn, 'wuji_glove', item['package'].path) != item['package']:
                raise ValueError('Official package changed during device preparation')
            self._transfer_item(token, sn)

    def finish_transfer(self, token):
        """Release only this handover; never renew an expired UI capability."""
        with self.lock:
            transfer = self._transfer
            if transfer is None or not _token(token) or not secrets.compare_digest(token, transfer['token']):return
            self._transfer = None
            self.state['busy'] = False
            if not transfer['claimed']:
                try:fresh = transfer['item']['created_at'] <= self._now() < transfer['item']['expires_at']
                except ValueError:fresh = False
                if not fresh:
                    self._prepared = None
                    self.state.update(package=None, phase='listed')

    def validate_claim(self, descriptor):
        if type(descriptor) is not dict:raise ValueError('Invalid server package claim')
        with self.lock:
            item = self._claims.get(descriptor.get('claim_id'))
            if item is None or descriptor != self._descriptor(item['package'], descriptor['claim_id'], item['reservation_token']):
                raise ValueError('Official package claim changed')
            package = self.validate(item['entry'], item['package'].serial, 'wuji_glove', item['package'].path)
            if package != item['package']:raise ValueError('Official package changed')
            return copy.deepcopy(descriptor)

    def release_owner(self, reservation_token):
        """Forget only memory claims after the host proves its lease released.

        Cached archives are retained, including those held by unknown writers.
        This method performs no filesystem, network, or device operation.
        """
        if type(reservation_token) is not str:return
        with self.lock:
            for claim_id in [key for key, value in self._claims.items() if value['reservation_token'] == reservation_token]:
                self._claims.pop(claim_id, None)
