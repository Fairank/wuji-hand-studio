"""Readable export of an actual CLI report. Never fabricates device results."""
from datetime import datetime, timezone


def report_text(report):
    if not isinstance(report, dict):
        raise ValueError('Expected a completed diagnostic report')
    finished = report.get('finished')
    stamp = 'unknown'
    if type(finished) in (int, float):
        try:
            stamp = datetime.fromtimestamp(finished, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            # A damaged saved timestamp must not discard the official output.
            pass
    header = [
        'WUJI Doctor / 官方诊断原始报告',
        'Time (UTC) / 时间: ' + stamp,
        'Controller / 控制端: ' + str(report.get('controller', 'unknown')),
        'Exit code / 官方退出码: ' + str(report.get('exit_code', 'unknown')),
        '退出码 0 不代表全部检查通过；警告、失败和跳过请以官方原文为准。',
        'Exit code 0 does not imply all checks passed. Review warnings, failures and skipped checks.',
        '', '=== STDOUT / 官方标准输出（原文） ===',
        report.get('stdout', ''),
        '', '=== STDERR / 官方标准错误（原文） ===',
        report.get('stderr', ''),
    ]
    if report.get('parse_error'):
        header += ['', 'Workbench parse error / 工作台解析错误: ' + str(report['parse_error'])]
    return '\n'.join(header) + '\n'


def counts(rows):
    result = dict(pass_count=0, warn_count=0, fail_count=0, skip_count=0, unknown_count=0)
    for row in rows:
        key = row.get('status', '') + '_count'
        if key in result:
            result[key] += 1
    return result
