"""Per-user Windows Credential Manager storage. Never serialize passwords to config/API."""
import ctypes
from ctypes import wintypes
import sys

class Credential(ctypes.Structure):
    _fields_ = [('Flags',wintypes.DWORD),('Type',wintypes.DWORD),
        ('TargetName',wintypes.LPWSTR),('Comment',wintypes.LPWSTR),
        ('LastWritten',wintypes.FILETIME),('CredentialBlobSize',wintypes.DWORD),
        ('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),
        ('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),
        ('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]

def target(config):
    from bridge_config import validate
    c=validate(config)
    if c['mode']!='ssh' or not c['host'] or not c['username']:
        raise ValueError('Fill in SSH host and user first / 先填写 SSH 主机和用户')
    return f"HandWorkbench/controller/{c['host'].lower()}:{c['port']}/{c['username']}"

def _api():
    if sys.platform!='win32':raise RuntimeError('Saved passwords currently require Windows / 此系统请使用 SSH 密钥')
    dll=ctypes.WinDLL('Advapi32.dll',use_last_error=True)
    dll.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    dll.CredReadW.restype=wintypes.BOOL
    dll.CredWriteW.argtypes=[ctypes.POINTER(Credential),wintypes.DWORD];dll.CredWriteW.restype=wintypes.BOOL
    dll.CredDeleteW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD];dll.CredDeleteW.restype=wintypes.BOOL
    dll.CredFree.argtypes=[ctypes.c_void_p];dll.CredFree.restype=None
    return dll

def read(config):
    if sys.platform!='win32' or config.get('mode')!='ssh' or not config.get('host') or not config.get('username'):return None
    api=_api();result=ctypes.POINTER(Credential)()
    if not api.CredReadW(target(config),1,0,ctypes.byref(result)):
        if ctypes.get_last_error()==1168:return None
        raise RuntimeError('Cannot read Windows credential / 无法读取 Windows 凭据')
    try:return ctypes.string_at(result.contents.CredentialBlob,result.contents.CredentialBlobSize).decode('utf-16-le')
    finally:api.CredFree(result)

def save(config,password):
    if not isinstance(password,str) or not password or '\0' in password or len(password.encode('utf-16-le'))>2560:
        raise ValueError('Invalid SSH password / SSH 密码为空或过长')
    name=target(config);raw=password.encode('utf-16-le');buf=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
    value=Credential(Type=1,TargetName=name,UserName=config['username'],CredentialBlobSize=len(raw),CredentialBlob=buf,Persist=2)
    try:
        if not _api().CredWriteW(ctypes.byref(value),0):raise RuntimeError('Cannot save Windows credential / 无法保存 Windows 凭据')
    finally:ctypes.memset(buf,0,len(raw))

def forget(config):
    if not _api().CredDeleteW(target(config),1,0) and ctypes.get_last_error()!=1168:
        raise RuntimeError('Cannot delete Windows credential / 无法删除 Windows 凭据')

def status(config):
    return dict(supported=sys.platform=='win32',saved=read(config) is not None)
