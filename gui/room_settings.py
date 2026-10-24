"""Room-local participants and model choices over the shared saved-agent library."""
import copy
import uuid
from engines import preferences
from context_settings import context_settings
from optimization_settings import optimization_settings,experiment_status


class RoomSettings:
    def __init__(self,store):self.store=store

    def seed(self,rid,defaults,legacy=False):
        with self.store.lock:
            if self.store.configuration(rid) is None:
                _,active=preferences.resolve_team(defaults['agents'],defaults['team'] if legacy else [])
                self.store.save_configuration(rid,{'team':copy.deepcopy(defaults['team']) if legacy else [],
                    'supervisor':copy.deepcopy(defaults['supervisor']),
                    'pause_before_execution':defaults['pause_before_execution'],
                    'executor':next((m['id'] for m in active if m['backend']=='deepseek'),None),
                    'executor_choice':'auto','context_epoch':0})

    def migrate(self,defaults):
        for room in self.store.rooms():
            self.seed(room['id'],defaults,legacy=True);self.load(room['id'],defaults)

    def load(self,rid,defaults=None):
        defaults=preferences.load() if defaults is None else defaults
        with self.store.lock:
            self.seed(rid,defaults,legacy=True)
            stored=self.store.configuration(rid)
            if 'executor' not in stored:
                _,active=preferences.resolve_team(defaults['agents'],stored['team'])
                stored['executor']=next((m['id'] for m in active if m['backend']=='deepseek'),None)
                stored['context_epoch']=0
                stored['executor_choice']='auto'
                self.store.save_configuration(rid,stored)
        result=copy.deepcopy(defaults)
        result.update(stored)
        result['context']=context_settings(stored.get('context'))
        result['optimization']=optimization_settings(stored.get('optimization'))
        result['optimization_experiments']=experiment_status()
        result['logging_enabled']=stored.get('logging_enabled',True)
        result.setdefault('supervisor_context','state');result.setdefault('resume_words',['继续']);result.setdefault('discussion_tolerance',3)
        result['team'],result['members']=preferences.resolve_team(result['agents'],stored['team'])
        result['room_id']=rid
        return result

    def update(self,rid,body):
        if not isinstance(body,dict) or set(body)-{'team','supervisor','pause_before_execution','executor','context','supervisor_context','resume_words','discussion_tolerance','optimization','logging_enabled'}:
            raise ValueError('协作室设置格式无效。')
        with self.store.lock:
            selected=self.load(rid)
            controls=context_settings(body.get('context',selected['context']))
            optimization=optimization_settings(body.get('optimization'),selected['optimization'])
            logging_enabled=body.get('logging_enabled',selected['logging_enabled'])
            if type(logging_enabled) is not bool:raise ValueError('协作日志必须是开关。')
            extra={k:body.get(k,selected[k]) for k in ['supervisor_context','resume_words','discussion_tolerance']}
            if extra['supervisor_context'] not in ['state','full']:raise ValueError('监工上下文模式无效。')
            if not isinstance(extra['resume_words'],list) or len(extra['resume_words'])>30 or any(not isinstance(w,str) or not 1<=len(w.strip())<=20 for w in extra['resume_words']):raise ValueError('续接短语最多 30 项，每项 1 至 20 字符。')
            extra['resume_words']=list(dict.fromkeys(w.strip() for w in extra['resume_words']))
            if type(extra['discussion_tolerance']) is not int or not 1<=extra['discussion_tolerance']<=5:raise ValueError('讨论容忍度应为 1 至 5。')
            team,members=preferences.resolve_team(selected['agents'],body.get('team',selected['team']))
            if not isinstance(body.get('supervisor',{}),dict):raise ValueError('监工设置无效。')
            config={**selected['supervisor'],**body.get('supervisor',{})}
            if set(config)-{'provider','model','effort','max_steps'}:raise ValueError('监工设置无效。')
            preferences.member_id(config['provider'])
            if config['provider'] not in {a['id'] for a in selected['agents']}:raise ValueError('请选择已保存的监工 Agent。')
            preferences.model_id(config['model'])
            config['effort']=preferences.effort_id(config.get('effort'))
            if type(config['max_steps']) is not int or not 2<=config['max_steps']<=32:raise ValueError('监工轮数应为 2 至 32。')
            pause=body.get('pause_before_execution',selected['pause_before_execution'])
            if type(pause) is not bool:raise ValueError('暂停设置无效。')
            executor=body.get('executor',selected.get('executor'))
            choice='manual' if 'executor' in body else selected.get('executor_choice','auto')
            active={m['id'] for m in members}
            if 'executor' in body and executor is not None and executor not in active:raise ValueError('执行者必须是此协作室中的成员。')
            if executor not in active:executor=None
            # First invitation of DeepSeek chooses the default, unless the user explicitly disabled it.
            if executor is None and choice=='auto':
                executor=next((m['id'] for m in members if m['backend']=='deepseek'),None)
            old_optimization=selected['optimization'] if selected['optimization']['enabled'] else {'enabled':False}
            new_optimization=optimization if optimization['enabled'] else {'enabled':False}
            epoch=selected.get('context_epoch',0)+(executor!=selected.get('executor') or old_optimization!=new_optimization)
            self.store.save_configuration(rid,dict(team=team,supervisor=config,pause_before_execution=pause,executor=executor,context_epoch=epoch,executor_choice=choice,context=controls,optimization=optimization,logging_enabled=logging_enabled,**extra))
        if hasattr(self.store,'logs'):self.store.logs.configure(rid,logging_enabled)
        return self.load(rid)

    def add(self,rid,aid):
        """Each invitation creates a separate participant, even for the same saved agent."""
        with self.store.lock:
            selected=self.load(rid)
            agent=next((a for a in selected['agents'] if a['id']==aid),None)
            if agent is None:raise ValueError('请选择 Agent 管理中已保存的 Agent。')
            occupied={r.get('name',next(a['name'] for a in selected['agents'] if a['id']==r['agent_id'])) for r in selected['team']}
            name=agent['name'];number=2
            while name in occupied:
                suffix=' '+str(number);name=agent['name'][:40-len(suffix)]+suffix;number+=1
            identities={r.get('id',r['agent_id']) for r in selected['team']}|{a['id'] for a in selected['agents']}
            mid='member-'+uuid.uuid4().hex
            while mid in identities:mid='member-'+uuid.uuid4().hex
            selected['team'].append(dict(id=mid,agent_id=aid,name=name,model=agent['model'],effort=agent['effort'],enabled=True))
            return self.update(rid,{'team':selected['team']})

    def reference(self,rid,mid):
        ref=next((r for r in self.load(rid)['team'] if r.get('id',r['agent_id'])==mid),None)
        if ref is None:raise ValueError('此成员实例不在当前协作室中。')
        return ref

    def membership(self,rid,aid,enabled):
        if type(enabled) is not bool:raise ValueError('请选择是否加入此协作室。')
        with self.store.lock:
            selected=self.load(rid)
            refs=selected['team'];ref=next((r for r in refs if r.get('id',r['agent_id'])==aid),None)
            if ref is None:
                agent=next((a for a in selected['agents'] if a['id']==aid),None)
                if agent is None or not enabled:raise ValueError('此成员实例不在当前协作室中。')
                ref=dict(agent_id=aid,model=agent['model'],effort=agent['effort']);refs.append(ref)
            ref['enabled']=enabled
            return self.update(rid,{'team':refs})

    def member(self,rid,aid,body):
        if not isinstance(body,dict) or not body or set(body)-{'name','model','effort'}:raise ValueError('成员设置无效。')
        with self.store.lock:
            selected=self.load(rid)
            ref=next((r for r in selected['team'] if r.get('id',r['agent_id'])==aid and r['enabled']),None)
            if ref is None:raise ValueError('此 Agent 不在当前协作室中。')
            ref.update(body)
            return self.update(rid,{'team':selected['team']})
