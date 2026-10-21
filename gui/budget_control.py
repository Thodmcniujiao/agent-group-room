"""Pause at a local context boundary and resume the same pending request."""
import asyncio


class ContextBudgetExceeded(RuntimeError):
    def __init__(self,estimated,limit,stage='组装请求'):
        self.estimated,self.limit,self.stage=estimated,limit,stage
        super().__init__(f'上下文估算 {estimated:,} tokens 超过本地预算 {limit:,}；可调整此协作室设置，或点击“超预算继续”。')


class BudgetGate:
    def __init__(self,store):self.store=store;self.pending={}
    def snapshot(self,rid):return [item['info'] for item in self.pending.get(rid,{}).values()]
    async def wait(self,turn,exceeded):
        if turn.forced:return
        event=asyncio.Event();items=self.pending.setdefault(turn.rid,{})
        info={'identity':turn.identity,'model':turn.model,'estimated':exceeded.estimated,'limit':exceeded.limit,'stage':exceeded.stage}
        items[turn.key]={'event':event,'info':info,'settings':turn.settings,'turn':turn}
        self.store.status(turn.rid,'budget_paused')
        self.store.event(turn.rid,{'type':'budget_wait','pending':self.snapshot(turn.rid)})
        self.store.message(turn.rid,'system',str(exceeded),phase='等待上下文预算处理')
        try:await event.wait()
        finally:
            items.pop(turn.key,None)
            if not items:self.pending.pop(turn.rid,None)
        if not self.pending.get(turn.rid):self.store.status(turn.rid,'running')
    def force(self,rid):
        items=self.pending.get(rid,{})
        if not items:raise ValueError('当前没有等待中的上下文请求。')
        for item in items.values():item['settings']['_force_context_budget']=True;item['event'].set()
        self.store.message(rid,'system','用户选择超预算继续：本次协作跳过本地上下文软上限；原请求继续，文件授权范围保持不变。',phase='本次预算覆盖')
    def update(self,rid,controls):
        from context import limits
        for item in self.pending.get(rid,{}).values():
            turn=item['turn'];turn.settings['context']=controls;turn.budget=limits(turn.source,turn.model,controls)
            turn.input_budget=turn.budget['soft']-(turn.budget['output'] if turn.budget['mode']=='shared' else 0)
            if item['info']['estimated']+turn.budget['margin']<=turn.input_budget:item['event'].set()
