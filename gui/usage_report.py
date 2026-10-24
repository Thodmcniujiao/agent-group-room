"""Read-only A1 reports. Inclusive totals and unknown measurements stay explicit."""
import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

FIELDS=('input','cached_read','cache_write','output','reasoning')
TOKEN_COMPONENTS=('instructions','originals','state','project','contract','tools','digests')


def report(records,by='member',timezone='Europe/London'):
    keys={'member':lambda r:r['identity'],'role':lambda r:r['role'],
        'session':lambda r:r['session_key']+':'+r['branch'],
        'day':lambda r:datetime.fromisoformat(r['created']).astimezone(ZoneInfo(timezone)).date().isoformat(),
        'adapter':lambda r:r['adapter'],
        'kind':lambda r:composition(r).get('request_kind','unspecified')}
    if by not in keys:raise ValueError('不支持此用量分组。')
    groups=defaultdict(list)
    for row in records:groups[keys[by](row)].append(row)
    result=[]
    for key,rows in sorted(groups.items()):
        tokens={field:{'known_sum':sum(r[field] for r in rows if r.get(field) is not None) if any(r.get(field) is not None for r in rows) else None,
            'unknown_requests':sum(r.get(field) is None for r in rows)} for field in FIELDS}
        paired=[r for r in rows if r.get('input') is not None and r.get('cached_read') is not None]
        denominator=sum(r['input'] for r in paired)
        observations=[composition(r).get('observations',{}) for r in rows]
        result.append({by:key,'requests':len(rows),'tokens':tokens,
            'cache_hit_rate':sum(r['cached_read'] for r in paired)/denominator if denominator else None,
            'cache_measured_requests':len(paired),
            'feed_counts':{mode:sum(o.get('feed_counts',{}).get(mode,0) for o in observations) for mode in ['full','menu','id','dual']}})
    return {'by':by,'timezone':timezone,'groups':result,'accounting':{
        'input':'total, includes cached_read and cache_write','output':'total, includes reasoning',
        'cache_hit_rate':'cached_read / input for requests with both known',
        'cost':'unverified; no billing prices or counterfactual baseline supplied',
        'net_savings':'unverified; representation estimates are not measured task savings'}}


def composition(row):
    value=row.get('composition') or {}
    return json.loads(value) if isinstance(value,str) else value
