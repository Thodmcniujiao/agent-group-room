"""Shadow estimates graduate only after twenty consecutive accurate samples."""
import json
import math


def estimate_v2(value):
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    cjk=sum(1 for ch in text if '\u3400'<=ch<='\u9fff' or '\uf900'<=ch<='\ufaff')
    return math.ceil(cjk+(len(text.encode('utf-8'))-3*cjk)/4)


def table(store):
    store.db.execute('CREATE TABLE IF NOT EXISTS estimate_calibration(adapter TEXT,model TEXT,data TEXT,PRIMARY KEY(adapter,model))')


def adapter(turn):
    from api_protocol import is_gemini
    from vertex_protocol import is_vertex
    return {'chatgpt':'codex','claude':'claude'}.get(turn.source['backend'],'gemini' if is_gemini(turn.source.get('base_url','')) or is_vertex(turn.source.get('base_url','')) else 'openai_compatible')


def state(turn):
    with turn.store.lock:
        table(turn.store)
        row=turn.store.db.execute('SELECT data FROM estimate_calibration WHERE adapter=? AND model=?',(adapter(turn),turn.model)).fetchone()
        value=json.loads(row[0]) if row else {}
        # Earlier samples were collected with a different full-request composition.
        return value if value.get('version')==2 else {'version':2,'samples':0,'streak':0,'value':1.0}


def observe(turn,actual,full_estimate):
    if not full_estimate or full_estimate<=0:return
    value=state(turn);predicted=full_estimate*value['value']
    accurate=abs(predicted-actual)/max(1,actual)<=0.2
    value['samples']+=1;value['streak']=value['streak']+1 if accurate else 0
    ratio=actual/full_estimate;value['value']=0.2*ratio+0.8*value['value']
    value.update(last_actual=actual,last_shadow=full_estimate,last_error=abs(predicted-actual)/max(1,actual))
    with turn.store.lock:
        turn.store.db.execute('INSERT INTO estimate_calibration VALUES(?,?,?) ON CONFLICT(adapter,model) DO UPDATE SET data=excluded.data',
            (adapter(turn),turn.model,json.dumps(value)));turn.store.db.commit()


def estimate_for(turn,value):
    from context import estimate
    stats=state(turn);shadow=estimate_v2(value)
    return math.ceil(shadow*stats['value']) if stats['samples']>=20 and stats['streak']>=20 else estimate(value)
