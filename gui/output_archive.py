"""Host-owned text archives and explicitly incomplete, bounded head/tail previews."""
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime,timezone

from optimization_settings import active

OUTPUT_TOOL={'name':'output_read','description':'按编号分页读取本房间已归档工具输出。offset/limit 单位为字符；执行者只可读同一执行身份的执行会话与宿主验收输出。',
    'parameters':{'type':'object','properties':{'id':{'type':'string'},'offset':{'type':'integer','minimum':0},
        'limit':{'type':'integer','minimum':1,'maximum':8000}},'required':['id'],'additionalProperties':False}}


def stamp():return datetime.now(timezone.utc).isoformat()


def save(db,lock,rid,owner,kind,command,exit_code,text,complete=True):
    identifier='out_'+uuid.uuid4().hex
    digest=hashlib.sha256(text.encode()).hexdigest()
    with lock:
        if db.execute('SELECT 1 FROM rooms WHERE id=?',(rid,)).fetchone() is None:raise ValueError('输出不属于有效协作室。')
        db.execute('INSERT INTO output_archives VALUES(?,?,?,?,?,?,?,?,?,?)',
            (identifier,rid,owner,kind,command,exit_code,text,digest,stamp(),int(complete)))
        db.commit()
    return {'id':identifier,'sha256':digest,'characters':len(text),'complete':bool(complete)}


def preview(text,reference,maximum=6000):
    if len(text)<=maximum:return text
    note=(f'\n[输出预览：省略中间内容，非完整证据；归档 {reference["id"]}，'
        f'{reference["characters"]} 字符；完整捕获={str(reference["complete"]).lower()}；'
        f'output_read(id="{reference["id"]}", offset=0)]\n')
    room=max(0,maximum-len(note));head=room//3
    return text[:head]+note+text[-(room-head):] if room else note[:maximum]


def present(store,rid,owner,command,exit_code,text,settings,kind='tool',complete=True):
    if not active(settings,'output_preview'):return text,None
    maximum=settings['optimization']['output_preview']['max_chars']
    reference=save(store.db,store.lock,rid,owner,kind,command,exit_code,text,complete)
    body=preview(text,reference,maximum)
    store.event(rid,{'type':'output_archived','output':reference,'owner_session':owner,'kind':kind,
        'original_characters':len(text),'preview_characters':len(body),'is_preview':body!=text})
    return body,reference


def read(db,lock,rid,session_key,branch,args,user=False):
    if not isinstance(args,dict) or set(args)-{'id','offset','limit'}:raise ValueError('输出读取参数无效。')
    identifier=args.get('id');offset=args.get('offset',0);limit=args.get('limit',8000)
    if not isinstance(identifier,str) or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=8000:
        raise ValueError('输出分页需要有效编号、非负 offset 和 1 至 8000 的 limit。')
    with lock:
        row=db.execute('SELECT * FROM output_archives WHERE id=? AND room=?',(identifier,rid)).fetchone()
        if row is None:raise ValueError('此协作室没有该输出。')
        if not user:
            session=db.execute('SELECT role,identity FROM agent_sessions WHERE key=? AND room=?',(session_key,rid)).fetchone()
            if session is None:raise ValueError('输出读取会话无效。')
            if session['role']=='executor' and row['owner_session'] not in [session_key,'host']:
                owner=db.execute('SELECT role,identity FROM agent_sessions WHERE key=? AND room=?',(row['owner_session'],rid)).fetchone()
                if owner is None or owner['role']!='executor' or owner['identity']!=session['identity']:
                    raise ValueError('执行者只能读取自己的执行会话与宿主验收输出。')
        text=row['text'];end=min(len(text),offset+limit)
        db.execute('INSERT INTO output_reads(room,session_key,branch,output_id,start,end,created) VALUES(?,?,?,?,?,?,?)',
            (rid,session_key,branch,identifier,min(offset,len(text)),end,stamp()));db.commit()
        return {'id':identifier,'text':text[offset:end],'offset':offset,'next_offset':end if end<len(text) else None,
            'total_characters':len(text),'sha256':row['sha256'],'capture_complete':bool(row['complete']),
            'exit_code':row['exit_code'],'kind':row['kind']}


def environment_archive():
    """Only the host passes these values to its pinned executor process."""
    if not os.environ.get('ROOM_DATABASE') or not os.environ.get('ROOM_OUTPUT_PREVIEW'):return None
    db=sqlite3.connect(os.environ['ROOM_DATABASE'],timeout=20,check_same_thread=False);db.row_factory=sqlite3.Row
    return db,threading.RLock(),os.environ['ROOM_ID'],os.environ['ROOM_SESSION'],int(os.environ['ROOM_OUTPUT_PREVIEW'])


def present_environment(command,exit_code,text,kind='tool',complete=True):
    context=environment_archive()
    if context is None:return text
    db,lock,rid,owner,maximum=context
    try:
        reference=save(db,lock,rid,owner,kind,command,exit_code,text,complete)
        return preview(text,reference,maximum)
    finally:db.close()
