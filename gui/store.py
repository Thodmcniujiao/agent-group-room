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
        ''')
        self.db.execute("UPDATE rooms SET status='interrupted' WHERE status IN ('running','paused')")
        self.db.execute("UPDATE messages SET status='interrupted' WHERE status='streaming'")
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
            self.db.commit()
        self.event(rid, {'type':'message','message':value})
        return mid

    def update(self, mid, text, status='streaming'):
        with self.lock:
            self.db.execute('UPDATE messages SET text=?,status=? WHERE id=?',(text[:100000],status,mid))
            row = self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
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
        items = self.messages(rid)[-20:]
        return '\n\n'.join(f"{m['provider']} ({m['phase']}): {m['text'][:4000]}" for m in items if m['status']=='complete')[-14000:]

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
