"""Start a controller with a validated, process-local parameter snapshot."""
import json
import os
import runpy
import sys
import types
from pathlib import Path


def install_parameters(source):
    from parameter_file import parse_parameters
    values = json.loads(source)
    if not isinstance(values, dict):
        raise ValueError('Expected a parameter object')
    checked = parse_parameters('\n'.join(f'{k} = {v!r}' for k, v in values.items()))
    module = types.ModuleType('motion_parameters')
    module.__dict__.update(checked)
    sys.modules['motion_parameters'] = module


if __name__ == '__main__':
    install_parameters(os.environ['WUJI_PARAMETERS_JSON'])
    runpy.run_path(str(Path(__file__).with_name('console_agent.py')), run_name='__main__')
