"""Pure normalization of already-observed device identities, never discovery.

No SDK/CLI imports, process creation, filesystem access or device operations.
Advertisements and serial hints never become verified connection identities.
All timestamps use wall-clock seconds; missing/future observations are stale.
"""
import math
import re
import time


_SERIAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')
_HAND2_HINT = re.compile(r'WH2([JK])[A-Z][0-9]{11}\Z')
_KNOWN_TYPES = {
    'wujihand2': ('hand', 'hand2'), 'hand2': ('hand', 'hand2'),
    'wujihand': ('hand', 'hand1'), 'wujihand1': ('hand', 'hand1'), 'hand1': ('hand', 'hand1'),
    'hand': ('hand', None), 'robotichand': ('hand', None),
    'wujiglove': ('glove', None), 'glove': ('glove', None),
    'wujiglove1': ('glove', 'glove1'), 'glove1': ('glove', 'glove1'),
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _clock(now, ttl):
    now = time.time() if now is None else now
    if not _number(now) or not _number(ttl) or ttl <= 0:
        raise ValueError('Expected a nonnegative current time and positive TTL')
    return float(now), float(ttl)


def _text(value, limit=512):
    return value.strip() if isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value) else ''


def _type(value):
    value = re.sub(r'[^a-z0-9]', '', _text(value, 120).lower())
    return _KNOWN_TYPES.get(value.removeprefix('devicetype'))


def _side(value):
    value = re.sub(r'[^a-z]', '', _text(value, 60).lower()).removeprefix('handedness')
    return {'left': 'left', 'lefthand': 'left', 'right': 'right', 'righthand': 'right'}.get(value)


def _hint(serial):
    match = _HAND2_HINT.fullmatch(serial)
    if match:
        return dict(kind='hand', generation='hand2', side='left' if match[1] == 'J' else 'right')
    if serial.startswith('WG1'):
        return dict(kind='glove', generation='glove1')
    if serial.startswith('WH1'):
        return dict(kind='hand', generation='hand1')
    return {}


def _model(kind, generation):
    return {'hand1': 'Wuji Hand 1', 'hand2': 'Wuji Hand 2', 'glove1': 'Wuji Glove 1'}.get(
        generation, {'hand': 'Wuji Hand', 'glove': 'Wuji Glove'}.get(kind, 'Unknown device'))


def _one(row, source, observed_at, now, ttl):
    if not isinstance(row, dict):
        return None, 'Device record must be an object'
    sn, serial = _text(row.get('sn'), 100), _text(row.get('serial'), 100)
    if sn and serial and sn != serial:
        return None, 'Conflicting sn and serial fields'
    serial = sn or serial
    if not _SERIAL.fullmatch(serial):
        return None, 'Missing or invalid serial'
    typed = []
    for key in ('device_type', 'type', 'model', 'device_name', 'kind', 'generation'):
        value = _type(row.get(key))
        if value:
            typed.append((key, value))
    kinds = {value[0] for _, value in typed}
    generations = {value[1] for _, value in typed if value[1] is not None}
    sides = {_side(row.get(key)) for key in ('handedness', 'side') if _side(row.get(key))}
    if len(kinds) > 1 or len(generations) > 1 or len(sides) > 1:
        return None, 'Conflicting explicit identity fields'
    hint = _hint(serial)
    kind = next(iter(kinds), None) or hint.get('kind', 'unknown')
    generation = next(iter(generations), None)
    if generation is None and hint.get('kind') == kind:
        generation = hint.get('generation')
    side = next(iter(sides), None) or (hint.get('side') if hint.get('kind') == kind else None)
    explicit = set()
    if kinds:
        explicit.add('kind')
    if generations:
        explicit.add('generation')
    if sides:
        explicit.add('side')
    address = _text(row.get('address')) or _text(row.get('ip'))
    addresses = sorted({_text(row.get(key)) for key in ('address', 'ip') if _text(row.get(key))})
    transport = _text(row.get('transport'), 80).lower().removeprefix('transport.')
    if not transport and address:
        transport = 'zenoh' if address.startswith('zenoh://') else 'usb' if address.startswith('usb') else 'udp' if address.startswith('udp/') else None
    observed_at = float(observed_at) if _number(observed_at) else None
    stale = observed_at is None or observed_at > now or now - observed_at >= ttl
    verified = (source in {'sdk_identity', 'glove_session_identity'} and row.get('identity_verified') is True
                and bool(kinds) and bool(sides) and (kind != 'hand' or bool(generations)))
    value = dict(serial=serial, sn=serial, kind=kind, generation=generation, side=side,
                 kind_source='device' if kinds else 'serial_hint' if hint.get('kind') else None,
                 side_source='device' if sides else 'serial_hint' if side else None,
                 generation_source='device' if generations else 'serial_hint' if generation else None,
                 model=_model(kind, generation), identity_source=source if explicit else 'serial_hint' if hint else None,
                 identity_verified=verified, source=source, sources=[source], passive=True,
                 observed_at=observed_at, last_seen=observed_at,
                 expires_at=observed_at + ttl if observed_at is not None else None,
                 stale=stale, in_use=False, official_available=source.startswith('official_') and not stale,
                 selectable=kind != 'unknown' and not stale, address=address or None,
                 addresses=addresses, transport=transport or None, conflict=False,
                 _explicit=explicit, _official_fresh=source.startswith('official_') and not stale)
    return value, None


def _merge(rows):
    devices, conflicts = [], []
    grouped = {}
    for row in rows:
        grouped.setdefault(row['serial'], []).append(row)
    for serial, observations in sorted(grouped.items()):
        bad = []
        for field in ('kind', 'generation', 'side'):
            # A serial hint cannot contradict or override device-reported
            # fields. Different explicit reports are never silently selected.
            values = {row[field] for row in observations if field in row['_explicit'] and row[field] is not None}
            if len(values) > 1:
                bad.append(field)
        if bad:
            conflicts.append(dict(serial=serial, fields=bad, sources=sorted({row['source'] for row in observations}), reason='Conflicting explicit device identities'))
            continue
        ranked = sorted(observations, key=lambda row: (not row['stale'], row['identity_verified'], row['_official_fresh'], row['observed_at'] or -1), reverse=True)
        chosen = dict(ranked[0])
        for field in ('kind', 'generation', 'side'):
            actual = next((row for row in ranked if field in row['_explicit'] and row[field] is not None), None)
            if actual:
                chosen[field] = actual[field]
                chosen['_explicit'] = chosen['_explicit'] | {field}
                if field == 'kind':
                    chosen['identity_source'] = actual['identity_source']
                    chosen['kind_source'] = 'device'
                if field == 'side':
                    chosen['side_source'] = 'device'
                if field == 'generation':
                    chosen['generation_source'] = 'device'
        # An explicit kind must not inherit an incompatible generation from a
        # different source's serial hint.
        if chosen['generation'] and not chosen['generation'].startswith(chosen['kind']):
            chosen['generation'] = chosen['generation_source'] = None
        if chosen['side_source'] == 'serial_hint' and _hint(serial).get('kind') != chosen['kind']:
            chosen['side'] = chosen['side_source'] = None
        chosen['sources'] = sorted({source for row in observations for source in row['sources']})
        chosen['addresses'] = sorted({address for row in ranked for address in row['addresses']})
        chosen['address'] = next((row['address'] for row in ranked if row['address']), None)
        chosen['identity_verified'] = any(row['identity_verified'] for row in observations)
        chosen['in_use'] = any(row['in_use'] for row in observations)
        chosen['official_available'] = any(row['_official_fresh'] for row in observations)
        chosen['_official_fresh'] = chosen['official_available']
        chosen['model'] = _model(chosen['kind'], chosen['generation'])
        chosen['selectable'] = chosen['kind'] != 'unknown' and not chosen['stale']
        distinct = {}
        for row in observations:
            for observation in row.get('_observations', [row]):
                key = tuple(observation.get(field) for field in (
                    'serial', 'source', 'kind', 'kind_source', 'generation', 'generation_source', 'side', 'side_source',
                    'identity_verified', 'observed_at', 'stale', 'in_use', 'official_available', 'address', 'transport'))
                key += (tuple(observation['addresses']),)
                distinct.setdefault(key, observation)
        chosen['_observations'] = list(distinct.values())
        devices.append(chosen)
    return devices, conflicts


def _public(row, include_observations=True):
    value = {key: item for key, item in row.items() if not key.startswith('_')}
    if include_observations:
        value['observations'] = [_public(item, False) for item in row.get('_observations', [row])]
    return value


def normalize_devices(rows, *, source, observed_at, now=None, ttl=30):
    """Normalize one bounded observation list without running discovery.

    Returns ``{devices, conflicts, rejected}``. Conflicting identities are
    excluded; duplicate transports for the same identity are merged.
    """
    now, ttl = _clock(now, ttl)
    source = _text(source, 80)
    if not source:
        raise ValueError('Observation source required')
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError('Expected at most 512 device observations')
    accepted, rejected = [], []
    for index, row in enumerate(rows):
        value, reason = _one(row, source, observed_at, now, ttl)
        if reason:
            rejected.append(dict(index=index, source=source, reason=reason))
        else:
            accepted.append(value)
    devices, conflicts = _merge(accepted)
    return dict(devices=[_public(row) for row in devices], conflicts=conflicts, rejected=rejected)


def build_inventory(viz=None, controller=None, glove=None, *, now=None, ttl=30):
    """Merge direct snapshots, without calling snapshot() or importing SDKs.

    ``viz.devices/scanned_at``, ``controller.devices/devices_at`` and
    ``glove.devices|hands/devices_at`` provide passive observations. Missing
    times stay stale. Exact currently-owned identities augment the inventory;
    unknown feedback never makes an advertisement live or verified.
    """
    now, ttl = _clock(now, ttl)
    viz, controller, glove = ({} if value is None else value for value in (viz, controller, glove))
    if not all(isinstance(value, dict) for value in (viz, controller, glove)):
        raise ValueError('Expected device state objects')
    observations, conflicts, rejected = [], [], []

    def add(rows, source, seen, error=False, owned=False):
        if not isinstance(rows, list) or len(rows) > 512:
            rejected.append(dict(source=source, reason='Invalid observation list'))
            return
        for index, raw in enumerate(rows):
            row, reason = _one(raw, source, seen, now, ttl)
            if reason:
                rejected.append(dict(source=source, index=index, reason=reason))
                continue
            if error:
                row.update(stale=True, selectable=False, official_available=False, _official_fresh=False)
            row['in_use'] = owned
            observations.append(row)

    add(viz.get('devices', []), 'official_cli', viz.get('scanned_at'), error=bool(viz.get('error')))
    add(controller.get('devices', []), 'controller_discovery', controller.get('devices_at'))
    add(glove.get('devices', []), 'glove_discovery', glove.get('devices_at'))
    add(glove.get('hands', []), 'glove_hand_discovery', glove.get('devices_at'))
    identified = controller.get('identified_device') or {}
    serial = identified.get('serial')
    profile = controller.get('device_profile') or {}
    if (controller.get('connection') == 'connected' and identified.get('source') == 'sdk_identity'
            and serial and serial == controller.get('device_id') and identified.get('profile') == profile.get('id')):
        add([dict(serial=serial, generation=profile.get('generation'), handedness=profile.get('side'), identity_verified=True)],
            'sdk_identity', None if controller.get('stale', True) else now, owned=True)
    glove_serial = glove.get('glove_serial')
    glove_profile = glove.get('profile')
    if glove.get('busy') is True and glove_serial and isinstance(glove_profile, str) and re.fullmatch(r'hand[12]_(left|right)', glove_profile):
        add([dict(serial=glove_serial, device_type='wuji_glove', handedness=glove_profile.rsplit('_', 1)[1], identity_verified=True)],
            'glove_session_identity', now if (glove.get('stream') or {}).get('fresh') is True else None, owned=True)
    feedback=glove.get('feedback') or {}
    hand_identity=feedback.get('identified_device') or {}
    hand_profile=hand_identity.get('profile')
    if (glove.get('busy') is True and feedback.get('connection')=='connected'
            and feedback.get('device_id') and hand_identity.get('serial')==feedback['device_id']
            and hand_identity.get('source')=='sdk_identity' and isinstance(hand_profile,str)
            and hand_profile==glove_profile and re.fullmatch(r'hand[12]_(left|right)',hand_profile)):
        generation,side=hand_profile.split('_')
        add([dict(serial=feedback['device_id'],generation=generation,side=side,identity_verified=True)],
            'sdk_identity',now if feedback.get('stale') is False else None,owned=True)
    # A running original Viewer owns its exact startup serial, but the CLI
    # does not report feedback freshness or turn its advertisement into a
    # verified SDK identity. Never update last_seen just because it is running.
    active_serial = (viz.get('device') or {}).get('sn') or (viz.get('device') or {}).get('serial')
    if viz.get('active') is True and viz.get('phase') == 'running' and active_serial:
        for row in observations:
            if row['serial'] == active_serial:
                row['in_use'] = True
    calibration=controller.get('calibration') or {}
    calibration_serial=calibration.get('serial')
    if calibration.get('running') is True and isinstance(calibration_serial,str) and _SERIAL.fullmatch(calibration_serial):
        # An official task owns this exact glove; it does not expose SDK
        # feedback freshness. Do not renew an advertisement or claim receiving.
        add([dict(serial=calibration_serial,device_type='wuji_glove',side=calibration.get('side'))],
            'official_calibration_session',None,owned=True)
    devices, merged_conflicts = _merge(observations)
    conflicts.extend(merged_conflicts)
    return dict(devices=[_public(row) for row in devices], conflicts=conflicts, rejected=rejected, updated_at=now, ttl_s=ttl)


def merge_inventories(inventories, *, now=None, ttl=30):
    """Merge canonical directories without discovery or refreshing observations.

    Each row keeps ``observations`` with its individual source and last_seen.
    Only those original timestamps can establish freshness; updated_at and
    combined ``sources`` never do. An explicit conflict in any input directory
    excludes that serial from the merged directory, even if another workspace
    has a nonconflicting observation. Inputs are not mutated.
    """
    now, ttl = _clock(now, ttl)
    if not isinstance(inventories, (list, tuple)) or len(inventories) > 64:
        raise ValueError('Expected at most 64 canonical device inventories')
    accepted, rejected, prior_conflicts = [], [], []
    total = 0
    for inventory_index, inventory in enumerate(inventories):
        if not isinstance(inventory, dict):
            raise ValueError('Expected canonical device inventory objects')
        rows = inventory.get('devices', [])
        if not isinstance(rows, list) or len(rows) > 512:
            raise ValueError('Expected at most 512 canonical devices per inventory')
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                rejected.append(dict(inventory=inventory_index, index=index, reason='Invalid canonical device'))
                continue
            leaves = row.get('observations', [row])
            if not isinstance(leaves, list) or not leaves or len(leaves) > 16384:
                rejected.append(dict(inventory=inventory_index, index=index, reason='Invalid canonical observations'))
                continue
            for leaf in leaves:
                total += 1
                if total > 16384:
                    raise ValueError('Too many canonical device observations')
                if not isinstance(leaf, dict):
                    rejected.append(dict(inventory=inventory_index, index=index, reason='Invalid canonical observation'))
                    continue
                source = _text(leaf.get('source'), 80)
                if not source:
                    rejected.append(dict(inventory=inventory_index, index=index, reason='Missing canonical observation source'))
                    continue
                # Canonical model names describe the chosen result. They are
                # never parsed again as new device-reported identity fields.
                raw = dict(serial=leaf.get('serial'), sn=leaf.get('sn'),
                           address=leaf.get('address'), transport=leaf.get('transport'),
                           identity_verified=leaf.get('identity_verified'))
                kind_source = leaf.get('kind_source')
                if kind_source == 'device' or (kind_source is None and leaf.get('identity_source') == source):
                    raw['kind'] = leaf.get('kind')
                if leaf.get('generation_source') == 'device':
                    raw['generation'] = leaf.get('generation')
                if leaf.get('side_source') == 'device':
                    raw['side'] = leaf.get('side')
                item, reason = _one(raw, source, leaf.get('last_seen'), now, ttl)
                if reason or item['serial'] != row.get('serial'):
                    rejected.append(dict(inventory=inventory_index, index=index, source=source,
                                         reason=reason or 'Canonical observation serial differs from device'))
                    continue
                # Error/invalid observations remain stale even if their old
                # timestamp happens to lie within this call's TTL.
                stale = item['stale'] or leaf.get('stale') is not False
                official = source.startswith('official_') and leaf.get('official_available') is True and not stale
                item.update(stale=stale, selectable=item['kind'] != 'unknown' and not stale,
                            in_use=leaf.get('in_use') is True,
                            official_available=official, _official_fresh=official)
                addresses = leaf.get('addresses', [])
                if isinstance(addresses, list):
                    item['addresses'] = sorted(set(item['addresses']) | {
                        _text(address) for address in addresses[:512] if _text(address)})
                accepted.append(item)
        input_conflicts, input_rejected = inventory.get('conflicts', []), inventory.get('rejected', [])
        if not isinstance(input_conflicts, list) or not isinstance(input_rejected, list):
            raise ValueError('Expected canonical conflict and rejection lists')
        for conflict in input_conflicts:
            if isinstance(conflict, dict) and _SERIAL.fullmatch(_text(conflict.get('serial'), 100)):
                prior_conflicts.append(dict(serial=conflict['serial'],
                    fields=[field for field in ('kind', 'generation', 'side') if field in conflict.get('fields', [])],
                    sources=sorted({_text(source, 80) for source in conflict.get('sources', []) if _text(source, 80)}),
                    reason='Conflicting explicit device identities'))
        for rejection in input_rejected:
            if isinstance(rejection, dict):
                rejected.append(dict(inventory=inventory_index, source=_text(rejection.get('source'), 80),
                                     reason=_text(rejection.get('reason'), 256) or 'Rejected input observation'))
    devices, new_conflicts = _merge(accepted)
    by_serial = {}
    for conflict in prior_conflicts + new_conflicts:
        previous = by_serial.setdefault(conflict['serial'], dict(serial=conflict['serial'], fields=[], sources=[],
            reason='Conflicting explicit device identities'))
        previous['fields'] = sorted(set(previous['fields']) | set(conflict['fields']))
        previous['sources'] = sorted(set(previous['sources']) | set(conflict['sources']))
    return dict(devices=[_public(row) for row in devices if row['serial'] not in by_serial],
                conflicts=[by_serial[serial] for serial in sorted(by_serial)], rejected=rejected,
                updated_at=now, ttl_s=ttl)
