"""Execution transport/runtime faults are host work, even after a job has started."""
import re


def runtime_fault(error,*,stage='execution',started=True,known_only=False):
    text=str(error);lower=text.lower();causes=[]
    if any(s in lower for s in ['workspace mutation requires review','unacknowledged mutation',
            'do not retry','待核实的写入','恢复记录']):causes.append('mutation_review')
    if 'output_truncated' in lower:causes.append('output_truncated')
    if 'max_turns' in lower or 'tool round limit' in lower:causes.append('tool_round_limit')
    if 'contextbudgetexceeded' in lower or 'context budget' in lower:causes.append('context_budget')
    if not causes and known_only:return None
    value={'code':causes[0] if causes else 'executor_runtime','causes':causes or ['executor_runtime'],
        'stage':stage,'started':bool(started),'automatic_retry':not started}
    match=re.search(r'max_turns\s*\((\d+)\)',text,re.I)
    if match:value['tool_rounds']=int(match[1])
    return value


def describe_fault(fault):
    notes={
        'mutation_review':'执行器留下待核实写入。保留文件和恢复记录；先在“恢复记录”核对，不能重复派发写入。',
        'output_truncated':'模型输出达到执行器上限而截断。请核对实际输出上限；提高房间预算不一定提高执行器硬上限。',
        'tool_round_limit':'执行器已耗尽本次工具轮数，宿主保留当前文件并运行原契约验收。',
        'context_budget':'请求超过上下文预算，需核对上下文设置。',
        'executor_runtime':'执行器运行异常，宿主核对环境与现有文件。'}
    return '\n'.join(notes[c] for c in fault['causes'])+'\n本次已停止；复查只运行宿主检查，不调用监工或执行者，不重派已启动的契约。'
