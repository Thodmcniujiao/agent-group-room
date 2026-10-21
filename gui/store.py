"""Local durable rooms and replayable events."""
import json
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory/'rooms.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
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
        ''')
        self.db.execute("UPDATE rooms SET status='interrupted' WHERE status IN ('running','paused','budget_paused')")
        self.db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
        import hashlib
        for row in self.db.execute("SELECT id,room,text FROM messages WHERE status='complete' AND id NOT IN (SELECT message_id FROM message_sequence) ORDER BY created,rowid").fetchall():
            self.db.execute('INSERT INTO message_sequence(message_id,room,hash) VALUES(?,?,?)',
                (row['id'],row['room'],hashlib.sha256(row['text'].encode()).hexdigest()))
        self.db.commit()

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

    def message(self, rid, provider, text='', model='', phase='', status='complete'):
        mid = uuid.uuid4().hex
        value = dict(id=mid,room=rid,provider=provider,model=model,phase=phase,text=text,status=status,created=now())
        with self.lock:
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',tuple(value.values()))
            if status=='complete':self._sequence(mid,rid,text)
            self.db.commit()
        self.event(rid, {'type':'message','message':value})
        return mid

    def update(self, mid, text, status='streaming'):
        with self.lock:
            previous=self.db.execute('SELECT text,status FROM messages WHERE id=?',(mid,)).fetchone()
            if previous and previous['status']=='complete' and (previous['text']!=text or status!='complete'):
                raise ValueError('已完成原文不可改写，请追加新消息。')
            self.db.execute('UPDATE messages SET text=?,status=? WHERE id=?',(text,status,mid))
            row = self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if row and status=='complete':self._sequence(mid,row['room'],text)
            self.db.commit()
        if row:
            self.event(row['room'], {'type':'message','message':dict(row)})

    def messages(self, rid):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT * FROM messages WHERE room=? ORDER BY created',(rid,))]

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
            return [dict(r) for r in self.db.execute('''SELECT m.*,s.seq,s.hash FROM message_sequence s
                JOIN messages m ON m.id=s.message_id WHERE s.room=? AND s.seq>? AND s.seq<=?
                AND m.status='complete' ORDER BY s.seq LIMIT ?''',
                (rid,after,self.boundary(rid) if before is None else before,limit))]

    def session(self,key):
        with self.lock:
            row=self.db.execute('SELECT data FROM agent_sessions WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None

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
