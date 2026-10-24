"""Validated room-local context controls; one policy for every transport."""
DEFAULT_CONTEXT={'soft_tokens':128000,'output_tokens':32768,'margin_tokens':6000,
    'tool_rounds':16,'overflow':'rotate','maintain_at':0.6,'handoff_recent':8}


def context_settings(value=None):
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-set(DEFAULT_CONTEXT):raise ValueError('上下文设置格式无效。')
    result={**DEFAULT_CONTEXT,**value}
    for key,lo,hi in [('soft_tokens',4096,8000000),('output_tokens',1024,128000),('margin_tokens',0,64000),('tool_rounds',1,100)]:
        if type(result[key]) is not int or not lo<=result[key]<=hi:raise ValueError(f'{key} 需要在 {lo:,} 至 {hi:,} 之间。')
    if result['soft_tokens']<=result['output_tokens']+result['margin_tokens']+1024:
        raise ValueError('上下文预算必须大于输出预留、安全余量与 1,024 tokens 的输入空间之和。')
    if result['overflow'] not in ['rotate','pause']:raise ValueError('上下文溢出处理无效。')
    if type(result['maintain_at']) not in [int,float] or not 0.3<=result['maintain_at']<=0.9:raise ValueError('维护阈值应为 0.3 至 0.9。')
    if type(result['handoff_recent']) is not int or not 0<=result['handoff_recent']<=50:raise ValueError('交接原文条数应为 0 至 50。')
    return result
