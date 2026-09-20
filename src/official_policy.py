"""Documented Wuji Hand 2 Beta 2 commissioning and fault semantics.

This selects the control guide's initial-debug current example and documented
power-on gains. It is not a force calibration or a copy of a factory test.
"""
import math
import motion_parameters as user_parameters
from motion_parameters import CURRENT_LIMIT_A,KP,KD

CONTROL_SOURCE='https://docs.wuji.tech/docs/en/wuji-hand/latest/control-guide/'
FAULT_SOURCE='https://docs.wuji.tech/docs/en/wuji-hand/latest/troubleshooting/'
POLICY_ID='wuji_beta2_documented_commissioning_v1'
LOWER_RAD=[math.radians(x) for x in ([-68,-85,-60,-60]+[-60,-40,-60,-60]*4)]
UPPER_RAD=[math.radians(x) for x in ([74,40,90,90]+[90,40,120,90]*4)]


def classify_device_error(code,severity,name):
    """Use SDK describe_error output; unknown information is never a pass."""
    if type(code) is not int or code<0:return 'unknown'
    if code==0:return 'none'
    if not isinstance(name,str) or not name.strip() or name.lower().startswith('unknown'):
        return 'unknown'
    if severity=='Warning':return 'warning'
    if severity in {'DeferredStop','ImmediateStop','Fatal'}:return 'stop'
    return 'unknown'


def settings():
    return dict(id=POLICY_ID,current_limit_A=CURRENT_LIMIT_A,kp=KP,kd=KD,
        parameter_source=CONTROL_SOURCE,fault_source=FAULT_SOURCE,
        current_basis='official_initial_debug_example' if CURRENT_LIMIT_A==.5 else 'user_configuration',
        gains_basis='documented_power_on_defaults' if (KP,KD)==(5.,.01) else 'user_configuration',
        editable_parameters={key:value for key,value in vars(user_parameters).items() if key.isupper()},
        warning_behavior='record_without_software_stop',
        fault_behavior='request_disable_on_nonwarning_fault_or_unknown',
        firmware_deferred_stop_emulated=False)
