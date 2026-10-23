"""Small typed command surface; network clients never supply joint targets."""
import re

class CancellationNotDispatched(ValueError):
    """The exact-session preflight refused cancellation before any command."""

COMMANDS = {'glove_session','glove_scan','glove_hand_scan','glove_open','glove_prepare',
            'glove_follow','glove_keepalive','glove_stop','glove_disconnect','glove_unpair','glove_retarget','glove_solver'}

def validate_cleanup(value):
    """A worker release acknowledgement, never a new device-control command."""
    fields={'version','glove_serial','sdk_user_id','profile','glove_was_open',
            'subscription_closed','glove_disconnected','solver_cleanup',
            'user_restored','ownership_released','profile_lease_released','selection_lease_released','hand_released','retryable'}
    if not isinstance(value,dict) or set(value)!=fields or type(value.get('version')) is not int or value['version']!=1:
        raise ValueError('Controller glove cleanup acknowledgement is unavailable')
    for key in ('glove_serial','sdk_user_id'):
        if not isinstance(value[key],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,100}',value[key]):
            raise ValueError('Invalid glove cleanup identity')
    if not isinstance(value['profile'],str) or not re.fullmatch(r'hand[12]_(left|right)',value['profile']):
        raise ValueError('Invalid glove cleanup target profile')
    if value['solver_cleanup'] not in ('not_open','closed','sdk_no_close_api','unconfirmed'):
        raise ValueError('Invalid glove solver cleanup acknowledgement')
    if any(type(value[k]) is not bool for k in fields-{'version','glove_serial','sdk_user_id','profile','solver_cleanup'}):
        raise ValueError('Invalid glove cleanup confirmation')
    if value['glove_was_open'] and not value['glove_serial']:
        raise ValueError('Missing released glove identity')
    if value['retryable'] and value['solver_cleanup']!='unconfirmed' and all(value[k] for k in (
            'subscription_closed','glove_disconnected','user_restored','ownership_released',
            'profile_lease_released','selection_lease_released','hand_released')):
        raise ValueError('Glove cleanup acknowledgement has contradictory retry state')
    return dict(value)


def cleanup_confirmed(value):
    value=validate_cleanup(value)
    return value['solver_cleanup']!='unconfirmed' and all(value[k] for k in (
        'subscription_closed','glove_disconnected','user_restored',
        'ownership_released','profile_lease_released','selection_lease_released','hand_released'))


def validate(c):
    if not isinstance(c,dict) or c.get('name') not in COMMANDS:
        raise ValueError('Unknown glove operation')
    allowed={'name'}
    if c['name']=='glove_session':allowed|={'continuation','sdk_requirement'}
    if c['name']=='glove_open':allowed|={'serial','user_id','timeout_ms','retarget','solver'}
    if c['name']=='glove_solver':allowed|={'solver'}
    if c['name']=='glove_prepare':allowed|={'address','serial'}
    if c['name']=='glove_retarget':allowed|={'retarget'}
    if c['name']=='glove_follow':allowed|={'workspace_clear','lease'}
    if c['name']=='glove_keepalive':allowed|={'lease'}
    if set(c)-allowed:raise ValueError('Unexpected glove fields; joint targets are not accepted')
    if 'continuation' in c:
        from calibration_portability import validate_continuation
        validate_continuation(c['continuation'],path=True)
    if 'sdk_requirement' in c:
        from sdk_release import validate_requirement
        validate_requirement(c['sdk_requirement'])
    if 'solver' in c or c['name']=='glove_solver':
        solver=c.get('solver')
        if not isinstance(solver,dict) or set(solver)-{'engine','values'} or solver.get('engine') not in ('sdk','official_open'):
            raise ValueError('Unknown solver configuration')
        if solver['engine']=='official_open':
            from retarget_tuning import validate as validate_tuning
            validate_tuning(solver.get('values'))
    for key in ('serial','lease'):
        if key in c and (not isinstance(c[key],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',c[key])):
            raise ValueError('Invalid '+key)
    # The SDK's built-in Default user has the legitimate ID ''. The worker
    # still checks every selected ID against the SDK's current user list.
    if 'user_id' in c and (not isinstance(c['user_id'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,100}',c['user_id'])):
        raise ValueError('Invalid user_id')
    if c['name']=='glove_open':
        if 'retarget' in c:
            from retarget_settings import validate as validate_retarget
            validate_retarget(c['retarget'])
        if not c.get('serial'):raise ValueError('Select a discovered Wuji Glove')
        if type(c.get('timeout_ms')) is not int or not 100<=c['timeout_ms']<=2000:
            raise ValueError('Glove timeout must be 100–2000 ms')
    if c['name']=='glove_prepare':
        from console_agent import validate_command
        validate_command(dict(name='connect',address=c.get('address','')))
    if c['name']=='glove_retarget':
        from retarget_settings import validate as validate_retarget
        validate_retarget(c.get('retarget'))
    if c['name']=='glove_follow' and c.get('workspace_clear') is not True:
        raise ValueError('Confirm the hand workspace before following')
    return c
