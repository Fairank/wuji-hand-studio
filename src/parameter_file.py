"""Read numeric user settings without executing a Python configuration file."""
import ast
import math

KEYS={'KP','KD','CURRENT_LIMIT_A','PATH_SPEED_RAD_S','COMMAND_SPEED_RAD_S',
      'MIN_TRANSITION_S','POSE_HOLD_S','OFFICIAL_ENDPOINT_HOLD_S','OFFICIAL_RETURN_HOLD_S',
      'MAX_TRIAL_DURATION_S','PROBE_SPEED_RAD_S','COMMAND_RATE_HZ'}


def parse_parameters(source):
    values={}
    for node in ast.parse(source).body:
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            continue
        if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Name):
            raise ValueError('参数文件只填写数字赋值，不加入函数、导入或其他代码')
        key=node.targets[0].id
        if key not in KEYS or key in values:raise ValueError('参数名称未知或重复：'+key)
        try:value=ast.literal_eval(node.value)
        except (ValueError,TypeError):raise ValueError('参数必须是数字：'+key) from None
        if type(value) not in {int,float} or not math.isfinite(value):raise ValueError('需要有限数字：'+key)
        if key in {'KP','KD','CURRENT_LIMIT_A'}:
            if value<0:raise ValueError('SDK参数不能为负：'+key)
        elif value<=0:raise ValueError('时间与速度须大于0，才能生成有效轨迹：'+key)
        if key=='CURRENT_LIMIT_A' and value>2.:
            raise ValueError('CURRENT_LIMIT_A超过官方设备硬上限2.0 A')
        if key=='COMMAND_RATE_HZ' and value>1000.:
            raise ValueError('指令发送频率超过官方PUB支持的1000 Hz')
        values[key]=value
    if set(values)!=KEYS:raise ValueError('缺少参数：'+','.join(sorted(KEYS-set(values))))
    return values


def render_parameters(source,values):
    """Preserve comments; callers can replace numeric assignment values only."""
    parse_parameters(source)
    if not isinstance(values,dict) or set(values)!=KEYS:raise ValueError(f'需要完整的{len(KEYS)}项参数')
    for key,value in values.items():
        if type(value) not in {int,float} or not math.isfinite(value):raise ValueError('需要有限数字：'+key)
    parse_parameters('\n'.join(f'{key} = {value!r}' for key,value in values.items()))
    lines=source.splitlines(keepends=True)
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign):
            if node.value.lineno!=node.value.end_lineno:raise ValueError('每个参数值须写在同一行')
            index=node.value.lineno-1;raw=lines[index].encode('utf-8')
            lines[index]=(raw[:node.value.col_offset]+repr(values[node.targets[0].id]).encode()+raw[node.value.end_col_offset:]).decode('utf-8')
    result=''.join(lines)
    if parse_parameters(result)!=values:raise ValueError('参数写入校验不符')
    return result
