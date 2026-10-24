"""Local durable rooms and replayable events."""
import json
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
import uuid


class WorkflowConflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory/'rooms.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._live={};self._live_seq=0
        self.db.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS rooms(id TEXT PRIMARY KEY,title TEXT,project TEXT,status TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,room TEXT,provider TEXT,model TEXT,phase TEXT,text TEXT,status TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,room TEXT,data TEXT);
          CREATE TABLE IF NOT EXISTS policies(room TEXT PRIMARY KEY,text TEXT,revision INTEGER,updated TEXT);
          CREATE TABLE IF NOT EXISTS room_settings(room TEXT PRIMARY KEY,data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS message_sequence(seq INTEGER PRIMARY KEY AUTOINCREMENT,message_id TEXT UNIQUE,room TEXT,hash TEXT);
          CREATE INDEX IF NOT EXISTS message_room_sequence ON message_sequence(room,seq);
          CREATE TABLE IF NOT EXISTS agent_sessions(key TEXT PRIMARY KEY,room TEXT,identity TEXT,role TEXT,model TEXT,data TEXT,updated TEXT);
          CREATE TABLE IF NOT EXISTS context_deliveries(id TEXT PRIMARY KEY,room TEXT,identity TEXT,data TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS workflow_state(room TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL,updated TEXT);
          CREATE TABLE IF NOT EXISTS plans(room TEXT,plan_id TEXT,version INTEGER,text TEXT,sha256 TEXT,source_message_id TEXT,created TEXT,PRIMARY KEY(room,plan_id,version));
          CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,room TEXT,plan_id TEXT,version INTEGER,sha256 TEXT,who TEXT,kind TEXT,message_id TEXT,source TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS raw_responses(id TEXT PRIMARY KEY,room TEXT,identity TEXT,text TEXT,status TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS message_actions(message_id TEXT PRIMARY KEY,data TEXT);
          CREATE TABLE IF NOT EXISTS host_actions(id TEXT PRIMARY KEY,room TEXT,kind TEXT,actor TEXT,payload TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS approval_documents(id TEXT PRIMARY KEY,room TEXT,plan_ref TEXT,plan_sha256 TEXT,document_sha256 TEXT,data TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS usage_records(id INTEGER PRIMARY KEY,room TEXT,identity TEXT,role TEXT,session_key TEXT,branch TEXT,adapter TEXT,model TEXT,request_ref TEXT,input INTEGER,cached_read INTEGER,cache_write INTEGER,output INTEGER,reasoning INTEGER,status TEXT,composition TEXT,created TEXT,UNIQUE(session_key,branch,adapter,request_ref));
          CREATE TABLE IF NOT EXISTS tool_deliveries(session_key TEXT,branch TEXT,message_seq INTEGER,start INTEGER,end INTEGER,total INTEGER,PRIMARY KEY(session_key,branch,message_seq,start,end));
          CREATE TABLE IF NOT EXISTS menu_deliveries(session_key TEXT,branch TEXT,message_seq INTEGER,kind TEXT,PRIMARY KEY(session_key,branch,message_seq,kind));
          CREATE INDEX IF NOT EXISTS usage_by_room ON usage_records(room,id);
          CREATE TABLE IF NOT EXISTS message_summaries(message_id TEXT PRIMARY KEY,message_hash TEXT NOT NULL,data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS output_archives(id TEXT PRIMARY KEY,room TEXT NOT NULL,owner_session TEXT NOT NULL,kind TEXT NOT NULL,command TEXT NOT NULL,exit_code INTEGER,text TEXT NOT NULL,sha256 TEXT NOT NULL,created TEXT NOT NULL,complete INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS outputs_by_room ON output_archives(room,created);
          CREATE TABLE IF NOT EXISTS output_reads(id INTEGER PRIMARY KEY,room TEXT,session_key TEXT,branch TEXT,output_id TEXT,start INTEGER,end INTEGER,created TEXT);
        ''')
        if 'action_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(approvals)')}:
            self.db.execute('ALTER TABLE approvals ADD COLUMN action_id TEXT')
        self.db.execute("UPDATE rooms SET status='interrupted' WHERE status IN ('running','paused','budget_paused','queued')")
        self.db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
        import hashlib
        for row in self.db.execute("SELECT id,room,text FROM messages WHERE status='complete' AND id NOT IN (SELECT message_id FROM message_sequence) ORDER BY created,rowid").fetchall():
            self.db.execute('INSERT INTO message_sequence(message_id,room,hash) VALUES(?,?,?)',
                (row['id'],row['room'],hashlib.sha256(row['text'].encode()).hexdigest()))
        self.db.commit()

        from collaboration_log import CollaborationLogs
        self.logs=CollaborationLogs(self,directory/'collaboration-logs')

    def rooms(self):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT * FROM rooms ORDER BY created DESC')]

    def create(self, title, project):
        rid = uuid.uuid4().hex
        with self.lock:
            self.db.execute('INSERT INTO rooms VALUES(?,?,?,?,?)', (rid,title[:70] or '新的协作',project,'idle',now()))
            self.db.commit()
        return self.room(rid)

    def room(self, rid):
        with self.lock:
            row = self.db.execute('SELECT * FROM rooms WHERE id=?',(rid,)).fetchone()
            if row is None:
                raise ValueError('会话不存在。')
            return dict(row)

    def configuration(self,rid):
        with self.lock:
            self.room(rid)
            row=self.db.execute('SELECT data FROM room_settings WHERE room=?',(rid,)).fetchone()
            return json.loads(row['data']) if row else None

    def save_configuration(self,rid,value):
        with self.lock:
            self.room(rid)
            self.db.execute('INSERT INTO room_settings VALUES(?,?) ON CONFLICT(room) DO UPDATE SET data=excluded.data',
                (rid,json.dumps(value,ensure_ascii=False)))
            self.db.commit()

    def status(self, rid, status):
        with self.lock:
            self.db.execute('UPDATE rooms SET status=? WHERE id=?',(status,rid))
            self.db.commit()
        self.event(rid, {'type':'room_status','status':status})

    def message(self, rid, provider, text='', model='', phase='', status='complete',actions=None):
        mid = uuid.uuid4().hex
        value = dict(id=mid,room=rid,provider=provider,model=model,phase=phase,text=text,status=status,created=now())
        with self.lock:
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',tuple(value.values()))
            if actions:
                self.db.execute('INSERT INTO message_actions VALUES(?,?)',(mid,json.dumps(actions,ensure_ascii=False)))
            if status in ['complete','host_error']:self._sequence(mid,rid,text)
            self.db.commit()
        if actions:value['actions']=actions
        if status!='streaming':self.logs.event(rid,'message',value)
        self.event(rid, {'type':'message','message':value})
        return mid

    def update(self, mid, text, status='streaming',summary=None):
        with self.lock:
            previous=self.db.execute('SELECT text,status FROM messages WHERE id=?',(mid,)).fetchone()
            if previous and previous['status']=='complete' and (previous['text']!=text or status!='complete'):
                raise ValueError('已完成原文不可改写，请追加新消息。')
            self.db.execute('UPDATE messages SET text=?,status=? WHERE id=?',(text,status,mid))
            row = self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if row and status=='complete':self._sequence(mid,row['room'],text)
            if row and status=='complete' and summary is not None:self._save_summary(mid,text,summary)
            self.db.commit()
        if row:
            value={'type':'message','message':self._message_value(row)}
            if status=='streaming':
                with self.lock:
                    self._live_seq+=1
                    self._live.setdefault(row['room'],{})[mid]=(self._live_seq,value)
            else:
                self.logs.event(row['room'],'message',value['message'])
                self.event(row['room'],value)
                with self.lock:self._live.get(row['room'],{}).pop(mid,None)

    def messages(self, rid):
        with self.lock:
            return [self._message_value(row) for row in self.db.execute('SELECT * FROM messages WHERE room=? ORDER BY created',(rid,))]

    def _message_value(self,row):
        value=dict(row)
        summary=self.db.execute('SELECT data FROM message_summaries WHERE message_id=?',(value['id'],)).fetchone()
        if summary:
            value['author_summary']=json.loads(summary[0])
            from member_summary import vote_mismatch
            value['summary_vote_mismatch']=vote_mismatch(value)
        actions=self.db.execute('SELECT data FROM message_actions WHERE message_id=?',(value['id'],)).fetchone()
        if actions:value['actions']=json.loads(actions[0])
        return value

    def _save_summary(self,mid,text,value):
        import hashlib
        if value.get('source')!='self' or value.get('message_hash')!=hashlib.sha256(text.encode()).hexdigest():raise ValueError('摘要必须绑定原文哈希与作者来源。')
        old=self.db.execute('SELECT data FROM message_summaries WHERE message_id=?',(mid,)).fetchone()
        payload=json.dumps(value,ensure_ascii=False)
        if old and old[0]!=payload:raise ValueError('已存作者摘要不可改写。')
        self.db.execute('INSERT OR IGNORE INTO message_summaries VALUES(?,?,?)',(mid,value['message_hash'],payload))

    def set_summary(self,mid,value):
        with self.lock:
            message=self.message_by_id(mid)
            if message['status']!='complete' or message['provider'] in ['you','supervisor','system','host']:raise ValueError('只能为讨论成员完整回复附摘要。')
            self._save_summary(mid,message['text'],value);self.db.commit()
            updated=self.message_by_id(mid)
        self.event(message['room'],{'type':'message','message':updated})

    def message_by_id(self,mid,rid=None):
        with self.lock:
            row=self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if row is None or (rid is not None and row['room']!=rid):raise ValueError('原消息不在此协作室中。')
            return self._message_value(row)

    def live(self,rid,after=0):
        with self.lock:
            # Streaming snapshots are replayed on reconnect without changing the durable SSE cursor.
            rows=[(seq,value) for seq,value in self._live.get(rid,{}).values() if seq>after]
            return sorted(rows,key=lambda item:item[0])

    def event(self, rid, value):
        with self.lock:
            value['time'] = now()
            cursor = self.db.execute('INSERT INTO events(room,data) VALUES(?,?)',(rid,json.dumps(value,ensure_ascii=False)))
            self.db.commit()
            return cursor.lastrowid

    def events(self, rid, after):
        with self.lock:
            return [{'seq':row['seq'],**json.loads(row['data'])} for row in self.db.execute(
                'SELECT seq,data FROM events WHERE room=? AND seq>? ORDER BY seq LIMIT 400',(rid,after))]

    def history(self, rid):
        return '\n\n'.join(f"{m['provider']} ({m['phase']}): {m['text']}" for m in self.messages(rid) if m['status']=='complete')

    def _sequence(self,mid,rid,text):
        import hashlib
        self.db.execute('INSERT OR IGNORE INTO message_sequence(message_id,room,hash) VALUES(?,?,?)',
            (mid,rid,hashlib.sha256(text.encode()).hexdigest()))

    def boundary(self,rid):
        with self.lock:
            return self.db.execute('SELECT coalesce(max(seq),0) FROM message_sequence WHERE room=?',(rid,)).fetchone()[0]

    def originals(self,rid,after=0,before=None,limit=100000):
        with self.lock:
            rows=self.db.execute('''SELECT m.*,s.seq,s.hash,d.data AS summary_record FROM message_sequence s
                JOIN messages m ON m.id=s.message_id LEFT JOIN message_summaries d ON d.message_id=m.id AND d.message_hash=s.hash WHERE s.room=? AND s.seq>? AND s.seq<=?
                AND m.status IN ('complete','host_error') ORDER BY s.seq LIMIT ?''',
                (rid,after,self.boundary(rid) if before is None else before,limit)).fetchall()
            return [self.original_value(r) for r in rows]

    @staticmethod
    def original_value(row):
        value=dict(row);summary=value.pop('summary_record',None)
        if summary:value['author_summary']=json.loads(summary)
        return value

    def session(self,key):
        with self.lock:
            row=self.db.execute('SELECT data FROM agent_sessions WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None

    def latest_session(self,rid,identity,role):
        with self.lock:
            row=self.db.execute("SELECT data FROM agent_sessions WHERE room=? AND identity=? AND role=? AND key NOT LIKE '%:archive:%' ORDER BY updated DESC LIMIT 1",(rid,identity,role)).fetchone()
            return json.loads(row[0]) if row else None

    def load_workflow(self,rid):
        with self.lock:
            row=self.db.execute('SELECT revision,data FROM workflow_state WHERE room=?',(rid,)).fetchone()
            return (json.loads(row['data']),row['revision']) if row else (None,0)

    def legacy_evidence(self,rid):
        import re
        with self.lock:
            rows=self.db.execute("SELECT data FROM events WHERE room=? AND json_extract(data,'$.type')='evidence' ORDER BY seq",(rid,)).fetchall()
        records=[];seen=set();maximum=0
        for index,row in enumerate(rows):
            r=json.loads(row[0])['record'];match=re.fullmatch(r'E(\d+)',str(r.get('id','')))
            if match:maximum=max(maximum,int(match[1]))
            signature=(r.get('message_id') or ('event',index),r.get('agent'),r.get('kind'),r.get('error'))
            if signature in seen:continue
            seen.add(signature)
            record={k:v for k,v in r.items() if k not in ['reply','id','instruction']}
            record.update(legacy_id=r.get('id'),legacy=True,bound_to={'plan':None,'code':None});records.append(record)
        for index,r in enumerate(records,maximum+1):r['id']=f'E{index:02d}'
        return records,maximum+len(records)+1

    def save_workflow(self,rid,data,expect_revision):
        with self.lock:
            payload=json.dumps(data,ensure_ascii=False)
            if expect_revision==0:
                try:self.db.execute('INSERT INTO workflow_state VALUES(?,?,?,?)',(rid,1,payload,now()))
                except sqlite3.IntegrityError:raise WorkflowConflict('工作流已更新，请重新读取。') from None
            else:
                result=self.db.execute('UPDATE workflow_state SET revision=revision+1,data=?,updated=? WHERE room=? AND revision=?',(payload,now(),rid,expect_revision))
                if result.rowcount!=1:raise WorkflowConflict('工作流已更新，请重新读取。')
            self.db.commit();return expect_revision+1

    def save_plan(self,rid,plan_id,mid):
        import hashlib
        if not isinstance(plan_id,str) or not __import__('re').fullmatch(r'P\d+',plan_id):raise ValueError('方案编号应为 P1、P2 等。')
        with self.lock:
            msg=self.message_by_id(mid,rid)
            if msg['status']!='complete' or msg['provider'] in ['you','system','host','supervisor']:raise ValueError('方案必须来自成员的完整公开原文。')
            version=self.db.execute('SELECT coalesce(max(version),0)+1 FROM plans WHERE room=? AND plan_id=?',(rid,plan_id)).fetchone()[0]
            sha=hashlib.sha256(msg['text'].encode()).hexdigest()
            self.db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?)',(rid,plan_id,version,msg['text'],sha,mid,now()));self.db.commit()
            return self.plan(rid,f'{plan_id}@{version}')

    def plan(self,rid,ref):
        import re
        match=re.fullmatch(r'(P\d+)@(\d+)',str(ref))
        if not match:raise ValueError('方案引用应包含版本，例如 P2@3。')
        with self.lock:
            row=self.db.execute('SELECT * FROM plans WHERE room=? AND plan_id=? AND version=?',(rid,match[1],int(match[2]))).fetchone()
            if row is None:raise ValueError('方案版本不存在。')
            return {**dict(row),'ref':ref}

    def add_host_action(self,rid,kind,payload):
        self.room(rid)
        if kind not in ['approve','recheck']:raise ValueError('宿主动作无效。')
        identifier=uuid.uuid4().hex
        with self.lock:
            self.db.execute('INSERT INTO host_actions VALUES(?,?,?,?,?,?)',(identifier,rid,kind,'user',json.dumps(payload,ensure_ascii=False),now()))
            self.db.commit()
        self.event(rid,{'type':'host_action','action_id':identifier,'kind':kind,'actor':'user'})
        return identifier

    def save_approval_document(self,document):
        with self.lock:
            self.db.execute('INSERT INTO approval_documents VALUES(?,?,?,?,?,?,?)',tuple(json.dumps(document[k],ensure_ascii=False) if k=='data' else document[k] for k in ['id','room','plan_ref','plan_sha256','document_sha256','data','created']))
            self.db.commit()

    def approval_document(self,rid,identifier):
        with self.lock:
            row=self.db.execute('SELECT * FROM approval_documents WHERE id=? AND room=?',(identifier,rid)).fetchone()
            if row is None:raise ValueError('审批文档不存在或不属于此协作室。')
            return {**dict(row),'data':json.loads(row['data'])}

    def approval_document_for_plan(self,rid,ref):
        with self.lock:
            row=self.db.execute('SELECT id FROM approval_documents WHERE room=? AND plan_ref=? ORDER BY created DESC LIMIT 1',(rid,ref)).fetchone()
            return self.approval_document(rid,row['id']) if row else None

    def host_actions(self,rid):
        with self.lock:return [{**dict(r),'payload':json.loads(r['payload'])} for r in self.db.execute('SELECT * FROM host_actions WHERE room=? ORDER BY created',(rid,))]

    def host_action(self,identifier,rid):
        action=next((a for a in self.host_actions(rid) if a['id']==identifier),None)
        if action is None:raise ValueError('宿主动作不存在或不属于此协作室。')
        return action

    def add_approval(self,rid,ref,who,kind,mid=None,source='format',action_id=None):
        with self.lock:
            plan=self.plan(rid,ref)
            if source=='button':
                action=self.host_action(action_id,rid)
                if kind!='user' or mid is not None or action['actor']!='user' or action['kind']!='approve' or action['payload'].get('plan_ref')!=ref:raise ValueError('按钮批准必须绑定此版本的真实用户动作。')
            else:
                if action_id is not None:raise ValueError('原文批准不能冒用按钮来源。')
                msg=self.message_by_id(mid,rid)
                if msg['provider']!=('you' if kind=='user' else who) or msg['status']!='complete':raise ValueError('批准必须绑定本人完整原文。')
            identifier=uuid.uuid4().hex
            self.db.execute('INSERT INTO approvals(id,room,plan_id,version,sha256,who,kind,message_id,source,created,action_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identifier,rid,plan['plan_id'],plan['version'],plan['sha256'],who,kind,mid,source,now(),action_id))
            self.db.commit();return identifier

    def approvals(self,rid):
        with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM approvals WHERE room=? ORDER BY created',(rid,))]

    def archive_raw(self,rid,identity,text,status='complete'):
        with self.lock:
            identifier=uuid.uuid4().hex
            self.db.execute('INSERT INTO raw_responses VALUES(?,?,?,?,?,?)',(identifier,rid,identity,text,status,now()));self.db.commit()
            return identifier

    def record_usage(self,turn,adapter,request_ref,usage,status,composition):
        with self.lock:
            columns=['input','cached_read','cache_write','output','reasoning']
            values=[usage.get(k) for k in columns]
            self.db.execute('''INSERT INTO usage_records(room,identity,role,session_key,branch,adapter,model,request_ref,input,cached_read,cache_write,output,reasoning,status,composition,created)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_key,branch,adapter,request_ref) DO UPDATE SET
                input=excluded.input,cached_read=excluded.cached_read,cache_write=excluded.cache_write,output=excluded.output,reasoning=excluded.reasoning,status=excluded.status,composition=excluded.composition''',
                (turn.rid,turn.identity,turn.role,turn.key,turn.state['branch'],adapter,turn.model,request_ref,*values,status,json.dumps(composition,ensure_ascii=False),now()))
            self.db.commit()

    def usage(self,rid):
        with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM usage_records WHERE room=? ORDER BY id',(rid,))]

    def last_usage_id(self,rid):
        with self.lock:return self.db.execute('SELECT coalesce(max(id),0) FROM usage_records WHERE room=?',(rid,)).fetchone()[0]

    def mark_usage_effect(self,rid,identity,after_id,effective):
        with self.lock:
            for row in self.db.execute('SELECT id,composition FROM usage_records WHERE room=? AND identity=? AND id>?',(rid,identity,after_id)).fetchall():
                composition=json.loads(row['composition']);composition['workflow_effect']=bool(effective)
                self.db.execute('UPDATE usage_records SET composition=? WHERE id=?',(json.dumps(composition,ensure_ascii=False),row['id']))
            self.db.commit()

    def usage_summary(self,rid):
        grouped={}
        for record in self.usage(rid):
            key=(record['role'],record['adapter']);bucket=grouped.setdefault(key,{'role':key[0],'adapter':key[1],'requests':0,'no_effect_requests':0,'tokens':{}})
            bucket['requests']+=1
            if json.loads(record['composition']).get('workflow_effect') is False:bucket['no_effect_requests']+=1
            for field in ['input','cached_read','cache_write','output','reasoning']:
                counter=bucket['tokens'].setdefault(field,{'known_sum':None,'unknown_requests':0})
                if record[field] is None:counter['unknown_requests']+=1
                else:counter['known_sum']=(counter['known_sum'] or 0)+record[field]
        return list(grouped.values())

    def save_session(self,key,rid,identity,role,model,data):
        with self.lock:
            self.db.execute('INSERT INTO agent_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                (key,rid,identity,role,model,json.dumps(data,ensure_ascii=False),now()))
            self.db.commit()

    def delivery(self,rid,identity,data):
        identifier=uuid.uuid4().hex
        with self.lock:
            self.db.execute('INSERT INTO context_deliveries VALUES(?,?,?,?,?)',
                (identifier,rid,identity,json.dumps(data,ensure_ascii=False),now()))
            self.db.commit()
        self.event(rid,{'type':'context','provider':identity,'context':data,'delivery_id':identifier})
        return identifier

    def deliveries(self,rid,limit=50):
        with self.lock:
            return [dict(id=r['id'],identity=r['identity'],created=r['created'],**json.loads(r['data']))
                for r in self.db.execute('SELECT * FROM context_deliveries WHERE room=? ORDER BY rowid DESC LIMIT ?',(rid,limit))]

    def latest_event(self,rid,kind):
        with self.lock:
            row=self.db.execute("SELECT data FROM events WHERE room=? AND json_extract(data,'$.type')=? ORDER BY seq DESC LIMIT 1",(rid,kind)).fetchone()
            return json.loads(row['data']) if row else None

    def policy(self,rid,default):
        with self.lock:
            row=self.db.execute('SELECT * FROM policies WHERE room=?',(rid,)).fetchone()
            return dict(row) if row else {'room':rid,'text':default,'revision':0,'updated':None}

    def set_policy(self,rid,text):
        self.room(rid)
        if not isinstance(text,str) or not 1<=len(text.strip())<=16000:
            raise ValueError('请输入 1 至 16000 字符的合作方式。')
        with self.lock:
            self.db.execute('INSERT INTO policies VALUES(?,?,1,?) ON CONFLICT(room) DO UPDATE SET text=excluded.text,revision=policies.revision+1,updated=excluded.updated',(rid,text.strip(),now()))
            self.db.commit()
        value=self.policy(rid,text)
        self.event(rid,{'type':'policy_updated','policy':value})
        return value
