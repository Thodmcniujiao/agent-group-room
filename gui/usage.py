"""Per-request accounting; unknown fields remain null, never fabricated zeroes."""
import math
import uuid

FIELDS=('input','cached_read','cache_write','output','reasoning')


def number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0 else None


def normalize(adapter,raw,previous_total=None):
    raw=raw or {};result=dict.fromkeys(FIELDS)
    if adapter=='codex':
        total=raw.get('total')
        if not isinstance(total,dict):return result,previous_total
        keys=dict(input='inputTokens',cached_read='cachedInputTokens',output='outputTokens',reasoning='reasoningOutputTokens')
        old=previous_total or {};fresh=previous_total is None
        if any(number(total.get(k)) is not None and number(old.get(k)) is not None and total[k]<old[k] for k in keys.values()):old={};fresh=True
        for dest,key in keys.items():
            value=number(total.get(key));base=number(old.get(key))
            result[dest]=None if value is None or base is None and not fresh else max(0,value-(base or 0))
        return result,total.copy()
    if adapter=='claude':
        parts=[number(raw.get(k)) for k in ['input_tokens','cache_creation_input_tokens','cache_read_input_tokens']]
        result.update(input=sum(parts) if all(v is not None for v in parts) else None,
            cached_read=parts[2],cache_write=parts[1],output=number(raw.get('output_tokens')),
            reasoning=number(raw.get('thinking_tokens')))
    elif adapter=='gemini':
        out=number(raw.get('total_output_tokens'));thought=number(raw.get('total_thought_tokens'))
        result.update(input=number(raw.get('total_input_tokens')),output=out+thought if out is not None and thought is not None else out,
            cached_read=number(raw.get('total_cached_tokens',raw.get('total_cached_input_tokens'))),reasoning=thought)
    else:
        result.update(input=number(raw.get('prompt_tokens')),output=number(raw.get('completion_tokens')),
            cached_read=number((raw.get('prompt_tokens_details') or {}).get('cached_tokens',raw.get('prompt_cache_hit_tokens'))),
            cache_write=number(raw.get('cache_creation_input_tokens')),
            reasoning=number((raw.get('completion_tokens_details') or {}).get('reasoning_tokens')))
    return result,None


def record(turn,adapter,raw,status='complete',request_ref=None):
    if not hasattr(turn.store,'record_usage'):return
    usage,total=normalize(adapter,raw,turn.state['native'].get('usage_total'))
    existing=None
    if request_ref:
        with turn.store.lock:
            existing=turn.store.db.execute('SELECT * FROM usage_records WHERE session_key=? AND branch=? AND adapter=? AND request_ref=?',
                (turn.key,turn.state['branch'],adapter,request_ref)).fetchone()
        if existing and existing['status']=='complete' and status=='interrupted' and usage['input'] is None:return usage
    if adapter=='codex' and total is not None:
        turn.state['native']['usage_total']=total;turn.save()
    composition=dict(getattr(turn,'composition',{}))
    from usage_report import TOKEN_COMPONENTS
    estimate=sum(composition[k] for k in TOKEN_COMPONENTS if isinstance(composition.get(k),(int,float)))
    difference=None if usage['input'] is None else usage['input']-estimate
    composition['unattributed']=None if difference is None else max(0,difference)
    composition['estimate_overage']=None if difference is None else max(0,-difference)
    composition['host_estimate']=estimate
    request_ref=request_ref or uuid.uuid4().hex
    turn.store.record_usage(turn,adapter,request_ref,usage,status,composition)
    audit=getattr(turn,'audit',None)
    if audit:audit.usage(adapter,raw,usage,status,request_ref)
    if usage['input'] is not None:
        turn.state['last_request_input']=number((raw or {}).get('last',{}).get('inputTokens')) if adapter=='codex' else usage['input']
        if turn.state['last_request_input'] is None:turn.state['last_request_input']=usage['input']
        turn.save()
        from calibration import observe
        if not existing or existing['input'] is None:observe(turn,usage['input'],getattr(turn,'last_full_estimate',None))
    return usage
