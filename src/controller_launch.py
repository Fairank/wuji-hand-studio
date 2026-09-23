"""No shared parameter-file writes between concurrent device controllers."""
import json
from runtime_paths import DATA, initialize
from parameter_file import parse_parameters


def parameter_environment():
    initialize()
    values = parse_parameters((DATA/'motion_parameters.py').read_text(encoding='utf-8-sig'))
    return 'WUJI_PARAMETERS_JSON=' + json.dumps(values, separators=(',', ':'))
