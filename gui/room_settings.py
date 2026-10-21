"""Room-local participants and model choices over the shared saved-agent library."""
import copy
from engines import preferences


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
        result['team'],result['members']=preferences.resolve_team(result['agents'],stored['team'])
        result['room_id']=rid
        return result

    def update(self,rid,body):
        if not isinstance(body,dict) or set(body)-{'team','supervisor','pause_before_execution','executor'}:
            raise ValueError('协作室设置格式无效。')
        with self.store.lock:
            selected=self.load(rid)
            team,_=preferences.resolve_team(selected['agents'],body.get('team',selected['team']))
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
            active={r['agent_id'] for r in team if r['enabled']}
            if 'executor' in body and executor is not None and executor not in active:raise ValueError('执行者必须是此协作室中的成员。')
            if executor not in active:executor=None
            # First invitation of DeepSeek chooses the default, unless the user explicitly disabled it.
            if executor is None and choice=='auto':
                executor=next((a['id'] for a in selected['agents'] if a['id'] in active and a['backend']=='deepseek'),None)
            epoch=selected.get('context_epoch',0)+(executor!=selected.get('executor'))
            self.store.save_configuration(rid,dict(team=team,supervisor=config,pause_before_execution=pause,executor=executor,context_epoch=epoch,executor_choice=choice))
            return self.load(rid)

    def membership(self,rid,aid,enabled):
        if type(enabled) is not bool:raise ValueError('请选择是否加入此协作室。')
        with self.store.lock:
            selected=self.load(rid)
            agent=next((a for a in selected['agents'] if a['id']==aid),None)
            if agent is None:raise ValueError('请选择 Agent 管理中已保存的 Agent。')
            refs=selected['team'];ref=next((r for r in refs if r['agent_id']==aid),None)
            if ref is None:
                ref=dict(agent_id=aid,model=agent['model'],effort=agent['effort']);refs.append(ref)
            ref['enabled']=enabled
            return self.update(rid,{'team':refs})

    def member(self,rid,aid,body):
        if not isinstance(body,dict) or not body or set(body)-{'model','effort'}:raise ValueError('成员模型设置无效。')
        with self.store.lock:
            selected=self.load(rid)
            ref=next((r for r in selected['team'] if r['agent_id']==aid and r['enabled']),None)
            if ref is None:raise ValueError('此 Agent 不在当前协作室中。')
            ref.update(body)
            return self.update(rid,{'team':selected['team']})
