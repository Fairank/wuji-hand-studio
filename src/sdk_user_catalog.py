"""Read SDK wearer identities through the existing controller transport.

No device discovery, connection, user switch, model import or retargeter is
permitted here. Model availability comes from the SDK's own export manifest;
temporary exports are removed and never become calibration authority.
"""
import json
import tempfile
from pathlib import Path


def read_users(manager):
    users=manager.list_users()
    if not isinstance(users,list) or len(users)>64:raise ValueError('Invalid SDK wearer directory')
    seen=set();result=[]
    with tempfile.TemporaryDirectory(prefix='wuji-wearer-read-') as folder:
        for i,user in enumerate(users):
            ident=user.get('user_id') if isinstance(user,dict) else None
            if (not isinstance(ident,str) or len(ident)>128 or any(ord(c)<32 for c in ident)
                    or ident in seen or not isinstance(user.get('display_name'),str)
                    or type(user.get('is_default')) is not bool or user['is_default']!=(ident=='')):
                raise ValueError('Invalid SDK wearer identity')
            seen.add(ident)
            try:manifest=manager.export_user_data(ident,str(Path(folder)/(str(i)+'.zip')))
            except Exception as error:
                from wuji_sdk import WujiException
                # The pinned SDK cannot export an entirely empty user. Accept
                # only its exact missing-data error for this validated identity;
                # display names are not unique and never identify named users.
                label='Default' if user['is_default'] else ident
                if (not isinstance(error,WujiException)
                        or str(error)!='Internal error: no calibration data found for user '+label):raise
                models=dict(left=False,right=False)
            else:
                if not isinstance(manifest,dict) or manifest.get('owner_user_id')!=ident:
                    raise ValueError('SDK wearer export identity changed')
                parts=manifest.get('components')
                if not isinstance(parts,list):raise ValueError('Invalid SDK model manifest')
                models={side:any(isinstance(p,dict) and p.get('name')=='hand_model.'+side
                                and p.get('state')=='present' for p in parts) for side in ('left','right')}
            result.append(dict(user_id=ident,name=user['display_name'][:128],is_default=user['is_default'],models=models))
    return result


def main():
    from wuji_sdk import SdkManager
    return dict(version=1,users=read_users(SdkManager.instance()))


def validate_report(value):
    if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('users'),list) or len(value['users'])>64:
        raise ValueError('Invalid controller wearer directory')
    seen=set()
    for user in value['users']:
        if (not isinstance(user,dict) or set(user)!={'user_id','name','is_default','models'}
                or not isinstance(user['user_id'],str) or len(user['user_id'])>128
                or any(ord(c)<32 for c in user['user_id']) or user['user_id'] in seen
                or not isinstance(user['name'],str) or len(user['name'])>128
                or type(user['is_default']) is not bool or not isinstance(user['models'],dict)
                or set(user['models'])!={'left','right'} or any(type(v) is not bool for v in user['models'].values())):
            raise ValueError('Invalid controller wearer identity')
        seen.add(user['user_id'])
    return json.loads(json.dumps(value))
