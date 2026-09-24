"""Small typed command surface; network clients never supply joint targets."""
import re

COMMANDS = {'glove_session','glove_scan','glove_open','glove_prepare',
            'glove_follow','glove_keepalive','glove_stop','glove_disconnect','glove_retarget','glove_solver'}

def validate(c):
    if not isinstance(c,dict) or c.get('name') not in COMMANDS:
        raise ValueError('Unknown glove operation')
    allowed={'name'}
    if c['name']=='glove_open':allowed|={'serial','user_id','timeout_ms','retarget','solver'}
    if c['name']=='glove_solver':allowed|={'solver'}
    if c['name']=='glove_prepare':allowed|={'address','serial'}
    if c['name']=='glove_retarget':allowed|={'retarget'}
    if c['name']=='glove_follow':allowed|={'workspace_clear','lease'}
    if c['name']=='glove_keepalive':allowed|={'lease'}
    if set(c)-allowed:raise ValueError('Unexpected glove fields; joint targets are not accepted')
    if 'solver' in c or c['name']=='glove_solver':
        solver=c.get('solver')
        if not isinstance(solver,dict) or set(solver)-{'engine','values'} or solver.get('engine') not in ('sdk','official_open'):
            raise ValueError('Unknown solver configuration')
        if solver['engine']=='official_open':
            from retarget_tuning import validate as validate_tuning
            validate_tuning(solver.get('values'))
    for key in ('serial','user_id','lease'):
        if key in c and (not isinstance(c[key],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',c[key])):
            raise ValueError('Invalid '+key)
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
