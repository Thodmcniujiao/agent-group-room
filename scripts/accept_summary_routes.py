"""Exercise a separately started, explicitly identified summary UI fixture over HTTP."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,ProxyHandler


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--url',default='http://127.0.0.1:49342');parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if urlsplit(args.url).hostname not in ['127.0.0.1','localhost']:raise ValueError('Only a local fixture is supported')
    fixture=json.loads((args.state/'fixture.json').read_text(encoding='utf-8'))
    if fixture.get('live_model_calls') is not False:raise ValueError('Not an offline fixture')
    opener=build_opener(ProxyHandler({}));token=None;checks=[]
    def request(path,body=None,expected=200):
        headers={'content-type':'application/json'}
        if body is not None:headers['x-tiered-token']=token
        req=Request(args.url+'/api'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req,timeout=10) as response:status=response.status;value=json.load(response)
        except HTTPError as error:status=error.code;value=json.loads(error.read())
        if status!=expected:raise AssertionError((path,status,value))
        return value
    def passed(name):checks.append({'name':name,'passed':True})
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gui'))
    from version import VERSION
    health=request('/health');assert health['version']==VERSION;passed('version')
    token=request('/bootstrap')['token']
    rid=fixture['room'];other=fixture['other_room'];base='/rooms/'+rid
    assert request(base)['room']['title']=='摘要与回取 · 验收夹具'
    original=request(base+'/configuration')['room_settings'];other_settings=request('/rooms/'+other+'/configuration')['room_settings']
    assert original['optimization']['enabled'] and not other_settings['optimization']['enabled'];passed('room isolation and persisted defaults')
    for item in original['optimization_experiments']:
        result=request(base+'/configuration',{'optimization':{'experiments':{item['id']:True}}},400)
        assert '日后测试' in result['error']
    after=request(base+'/configuration')['room_settings'];assert after['context_epoch']==original['context_epoch'];passed('all nine research gates reject enable atomically')
    disabled=request(base+'/configuration',{'optimization':{'enabled':False}})['room_settings']
    assert not disabled['optimization']['enabled'];assert disabled['context_epoch']>original['context_epoch'];passed('master off persists and advances branch epoch')
    restored=request(base+'/configuration',{'optimization':original['optimization']})['room_settings']
    assert restored['optimization']==original['optimization'];passed('master on restores independent options')
    for group in ['member','role','session','day','adapter','kind']:
        value=request(base+'/optimization-report?by='+group)
        assert value['groups'][0]['tokens']['input']['known_sum']==1000
        assert value['groups'][0]['cache_hit_rate']==.8
    passed('six report views and inclusive cache accounting')
    request(base+'/optimization-report?by=unsupported',expected=400);passed('invalid grouping rejected')
    offset=0;restored='';pages=0
    while offset is not None:
        part=request(base+'/outputs/'+fixture['output_id']+'?offset='+str(offset))
        restored+=part['text'];offset=part['next_offset'];pages+=1
    assert hashlib.sha256(restored.encode()).hexdigest()==part['sha256']
    assert 'TAIL-ERROR' in restored and part['exit_code']==3 and pages>1;passed('all output pages restore the archive hash and real exit status')
    request('/rooms/'+other+'/outputs/'+fixture['output_id'],expected=400)
    request(base+'/outputs/'+fixture['output_id']+'?limit=999999',expected=400);passed('cross-room and oversized reads rejected')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'passed':True,'checks':checks,'output_pages':pages,'live_model_calls':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(checks),'output_pages':pages}))


if __name__=='__main__':main()
