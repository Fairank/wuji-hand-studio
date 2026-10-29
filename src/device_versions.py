"""Read existing SDK metadata and recognize documented update notifications.

These observations are display-only. They never authorize an upgrade, select a
firmware package, or open a device. Unknown metadata/report formats stay unknown.
"""
import json
import re


CHECK_DOCUMENTATION = 'https://docs.wuji.tech/docs/en/wuji-cli/latest/firmware-upgrade/'
_HEADINGS = ['SN', 'Device Type', 'Current', 'Latest', 'Status']
_DEVICE_TYPES = frozenset(('wuji_glove', 'wuji_hand', 'wuji_hand_2'))
_VERSION = re.compile(r'(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z')
_SGR = re.compile(r'\x1b\[[0-9;]*m')


def _plain(value, limit):
    return (type(value) is str and 0 < len(value) <= limit
            and value == value.strip() and all(32 <= ord(c) <= 126 for c in value))


def read_handle_version(handle, expected_sn):
    """Sample ``handle.info`` once on its owner thread; issue no SDK RPC.

    Official DeviceInfo exposes string serial_number and firmware_version. Do
    not replace this with version().get(), re-connect, or decode Hand 1's packed
    integer version. A metadata problem must not turn a good connection into a
    failure or attribute another device's firmware to the selected serial.
    """
    if not _plain(expected_sn, 128):
        return None
    try:
        info = handle.info
        if info is None:
            return None
        serial = info.serial_number
        version = info.firmware_version
        if serial != expected_sn or not _plain(serial, 128) or not _plain(version, 128):
            return None
        return dict(serial=serial, firmware_version=version, source='sdk_handle_info')
    except Exception:
        return None


def parse_official_check_json(text, expected_sn, expected_type=None):
    """Recognize the pinned CLI's captured exact-SN read-only check envelope.

    The ``devices``/``current_version`` contract was verified against the real
    2026.10.12 native CLI report. This produces a reminder, never a flash plan.
    Catalog/download/manifests are intentionally not interpreted here.
    """
    if (not _plain(expected_sn, 128) or type(text) is not str or len(text) > 262144
            or (expected_type is not None and (type(expected_type) is not str or expected_type not in _DEVICE_TYPES))):
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('Duplicate official report field')
            result[key] = value
        return result
    def invalid(value):raise ValueError('Non-finite official report value')
    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    except (TypeError, ValueError, RecursionError):
        return None
    if type(value) is not dict or set(value) != {'devices'}:
        return None
    devices = value['devices']
    # --sn is an exact single-device operation. A broader/mixed report is not
    # accepted as authority for either a reminder or the confirmation entry.
    if type(devices) is not list or len(devices) != 1 or type(devices[0]) is not dict:
        return None
    row = devices[0]
    fields = {'sn', 'device_type', 'current_version', 'latest_version', 'status', 'error'}
    if set(row) != fields or row['error'] is not None:
        return None
    serial, kind = row['sn'], row['device_type']
    current, latest, status = row['current_version'], row['latest_version'], row['status']
    if (serial != expected_sn or type(kind) is not str or kind not in _DEVICE_TYPES
            or (expected_type is not None and kind != expected_type)
            or type(current) is not str or type(latest) is not str
            or _VERSION.fullmatch(current) is None or _VERSION.fullmatch(latest) is None
            or status not in ('update available', 'up to date')):
        return None
    before, after = tuple(map(int,current.split('.'))), tuple(map(int,latest.split('.')))
    available = status == 'update available'
    if (available and after <= before) or (not available and after > before):
        return None
    return dict(serial=serial, device_type=kind, current=current, latest=latest,
                update_available=available)


def observe_official_check_failure(text, expected_sn, expected_type):
    """Keep exact-device facts from a failed check, never update authority.

    A catalog failure can coexist with a valid current firmware read. This
    separate observation must not enter the success/reminder/recovery parsers.
    In particular, a missing latest version cannot mean "up to date".
    """
    if (not _plain(expected_sn, 128) or type(expected_type) is not str or expected_type not in _DEVICE_TYPES
            or type(text) is not str or len(text) > 262144):
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('Duplicate official report field')
            result[key] = value
        return result
    def invalid(value):raise ValueError('Non-finite official report value')
    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    except (TypeError, ValueError, RecursionError):
        return None
    if type(value) is not dict or set(value) != {'devices'}:
        return None
    rows = value['devices']
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        return None
    row = rows[0]
    if (set(row) != {'sn', 'device_type', 'current_version', 'latest_version', 'status', 'error'}
            or row['sn'] != expected_sn or row['device_type'] != expected_type
            or row['status'] != 'failed'):
        return None
    current, latest, error = row['current_version'], row['latest_version'], row['error']
    if (current is not None and (type(current) is not str or _VERSION.fullmatch(current) is None)
            or latest is not None and (type(latest) is not str or _VERSION.fullmatch(latest) is None)
            or type(error) is not str or not error.strip() or len(error) > 8192
            or any(ord(c) < 32 and c not in '\n\t' or ord(c) == 127 for c in error)):
        return None
    return dict(serial=expected_sn, device_type=expected_type, current=current,
                latest=None, status='failed', error=error, catalog_available=False)


def parse_official_resource_json(text, expected_path):
    """Strict string-valued Glove resource response; never accepts a catalog."""
    if type(text) is not str or len(text) > 4096 or expected_path not in ('serial_number', 'firmware_version'):
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:raise ValueError('Duplicate resource field')
            result[key] = value
        return result
    def invalid(value):raise ValueError('Non-finite resource field')
    try:value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, TypeError, RecursionError):return None
    if type(value) is not dict or set(value) != {'path', 'value'} or value['path'] != expected_path:
        return None
    result = value['value']
    if not _plain(result, 128):return None
    if expected_path == 'serial_number':
        return result if re.fullmatch(r'WG1[JK][A-Z0-9]{12}', result) else None
    return result if _VERSION.fullmatch(result) else None


def parse_official_check_report(text, expected_sn, expected_type=None):
    """Read the verified JSON or the publicly documented human table only."""
    return (parse_official_check_json(text, expected_sn, expected_type)
            or parse_official_check_table(text, expected_sn, expected_type))


def parse_official_check_table(text, expected_sn, expected_type=None):
    """Recognize only the public CLI ``upgrade --check`` table, for reminders.

    The input must come from a successful, exact-serial, read-only official CLI
    check. JSON envelopes/keys are deliberately not guessed. The caller keeps
    CLI identity, observation time, device revision and ownership provenance;
    this parser's result is never a write plan or an upgrade completion verdict.
    """
    if (not _plain(expected_sn, 128) or type(text) is not str or len(text) > 262144
            or (expected_type is not None and (type(expected_type) is not str or expected_type not in _DEVICE_TYPES))):
        return None
    text = _SGR.sub('', text).replace('\r\n', '\n')
    if any(ord(c) < 32 and c not in '\n\t' or ord(c) == 127 for c in text):
        return None
    lines = text.splitlines()
    if len(lines) > 2048 or any(len(line) > 4096 for line in lines):
        return None

    def cells(line):
        line = line.strip()
        if not (line.startswith('│') and line.endswith('│')):
            return None
        result = [part.strip() for part in line[1:-1].split('┆')]
        return result if len(result) == 5 else None

    headers = [i for i, line in enumerate(lines) if cells(line) == _HEADINGS]
    if len(headers) != 1:
        return None
    observations = {}
    closed = False
    for line in lines[headers[0]+1:]:
        line = line.strip()
        if line.startswith('└') and line.endswith('┘'):
            closed = True
            break
        if ((line.startswith('╞') and line.endswith('╡'))
                or (line.startswith('├') and line.endswith('┤'))):
            continue
        row = cells(line)
        if row is None:
            return None
        serial, kind, current, latest, status = row
        if (not _plain(serial, 128) or serial in observations or kind not in _DEVICE_TYPES
                or _VERSION.fullmatch(current) is None or _VERSION.fullmatch(latest) is None
                or status not in ('update available', 'up to date')):
            return None
        before = tuple(int(p) for p in current.split('.'))
        after = tuple(int(p) for p in latest.split('.'))
        available = status == 'update available'
        if (available and after <= before) or (not available and after > before):
            return None
        observations[serial] = dict(serial=serial, device_type=kind, current=current,
                                    latest=latest, update_available=available)
    if not closed:
        return None
    observation = observations.get(expected_sn)
    if observation is None or (expected_type is not None and observation['device_type'] != expected_type):
        return None
    return observation
