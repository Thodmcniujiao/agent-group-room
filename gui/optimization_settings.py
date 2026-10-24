"""Room-local rollout policy. Research routes cannot masquerade as enabled features."""
import copy
import math

EXPERIMENTS = {
    'dual': ('双投递', '费用、误读和返工率的配对测试'),
    'headroom': ('Headroom', '固定版本、Windows 适配、保真与回取测试'),
    'repomap': ('Aider RepoMap', '工作树一致性、任务覆盖和预算测试'),
    'toon': ('TOON', '官方编码往返与模型引用测试'),
    'ccusage': ('ccusage', '同会话同时间窗与 A1 对账'),
    'comem': ('CoMem', '权重、量化内存、事实保留和下游成功率测试'),
    'context_mode': ('Context Mode', '契约权限、工具路由和误拦测试'),
    'claude_mem': ('Claude-Mem', '房间隔离、版本失效和注入成本测试'),
    'llmlingua': ('LLMLingua', '代码、路径、否定句和证据保真测试'),
}
DEFAULT_OPTIMIZATION = {
    'enabled': False,
    'summary': {'enabled': True, 'min_chars': 400, 'max_chars': 160,
        'budget_ratio': 0.15, 'max_entries': 30,
        'uses': {'supervisor_history': True, 'not_loaded': True, 'handoff': True, 'catch_up': True}},
    'output_preview': {'enabled': True, 'max_chars': 6000},
    'project_tools': True,
    'brevity': False,
    'experiments': {key: False for key in EXPERIMENTS},
}


def optimization_settings(value=None,base=None):
    def merge(default, supplied, path='optimization'):
        if supplied is None:supplied={}
        if not isinstance(supplied, dict) or set(supplied)-set(default):
            raise ValueError(path+' 设置格式无效。')
        result=copy.deepcopy(default)
        for key,item in supplied.items():
            if isinstance(default[key],dict):result[key]=merge(default[key],item,path+'.'+key)
            else:result[key]=item
        return result
    result=merge(merge(DEFAULT_OPTIMIZATION,base),value)
    def booleans(part):
        for key,item in part.items():
            if isinstance(item,dict):booleans(item)
            elif key in ['enabled','project_tools','brevity',*EXPERIMENTS] or key in DEFAULT_OPTIMIZATION['summary']['uses']:
                if type(item) is not bool:raise ValueError(key+' 必须是开关。')
    booleans(result)
    summary=result['summary']
    for key,lo,hi in [('min_chars',0,5000),('max_chars',40,400),('max_entries',1,100)]:
        if type(summary[key]) is not int or not lo<=summary[key]<=hi:raise ValueError(f'{key} 应为 {lo} 至 {hi} 的整数。')
    ratio=summary['budget_ratio']
    if type(ratio) not in [int,float] or not math.isfinite(ratio) or not 0.05<=ratio<=0.5:raise ValueError('摘要预算比例应为 0.05 至 0.5。')
    maximum=result['output_preview']['max_chars']
    if type(maximum) is not int or not 1000<=maximum<=50000:raise ValueError('输出预览应为 1000 至 50000 字符。')
    for key,wanted in result['experiments'].items():
        if wanted:raise ValueError(EXPERIMENTS[key][0]+' 尚待日后测试：'+EXPERIMENTS[key][1]+'；当前版本不允许启用。')
    return result


def active(settings, feature=None):
    controls=settings.get('optimization') or DEFAULT_OPTIMIZATION
    if not controls.get('enabled',False):return False
    if feature is None:return True
    part=controls.get(feature,DEFAULT_OPTIMIZATION.get(feature))
    return part.get('enabled',False) if isinstance(part,dict) else bool(part)


def experiment_status():
    return [{'id':key,'name':name,'enabled':False,'available':False,'status':'pending_validation',
        'reason':'待日后测试：'+test,'installed':None} for key,(name,test) in EXPERIMENTS.items()]


def refresh_for_call(store,rid,settings):
    """Apply switches between calls, without changing a running turn or its role contract."""
    if not hasattr(store,'configuration'):return settings
    stored=store.configuration(rid)
    if not stored or 'optimization' not in stored:return settings
    controls=optimization_settings(stored['optimization'])
    epoch=stored.get('context_epoch',settings.get('context_epoch',0))
    if settings.get('optimization')==controls and settings.get('context_epoch',0)==epoch:return settings
    return {**settings,'optimization':controls,'context_epoch':epoch}
