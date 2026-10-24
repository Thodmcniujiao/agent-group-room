"""Local payload continuity only; a prefix hash is not proof of a provider cache hit."""
import hashlib
import json


def observe(turn,messages,tools):
    def hashed(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    hashes=[hashed(m) for m in messages];schema=hashed(tools)
    previous=turn.state.get('prefix_observer')
    common=0
    if previous and previous['tools']==schema:
        for old,new in zip(previous['messages'],hashes):
            if old!=new:break
            common+=1
    info={'common_messages':common,'previous_messages':len(previous['messages']) if previous else 0,
        'changed':bool(previous and (previous['tools']!=schema or common<len(previous['messages']))),
        'scope':'host_payload_only; provider cache hit remains measured usage'}
    if not hasattr(turn,'composition'):turn.composition={}
    turn.composition.setdefault('observations',{})['prefix']=info
    turn.state['prefix_observer']={'messages':hashes,'tools':schema}
    turn.save()
    return info
