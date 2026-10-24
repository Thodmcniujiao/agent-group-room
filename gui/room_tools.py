"""Read-only MCP projection; no Store construction or database mutations."""
import json
import os
import sqlite3
import threading
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from context import read_originals
from delivery import receipt


class Archive:
    def __init__(self):
        self.db=sqlite3.connect(Path(os.environ['ROOM_DATABASE']).resolve().as_uri()+'?mode=ro',uri=True,check_same_thread=False)
        self.db.row_factory=sqlite3.Row;self.lock=threading.RLock()
        self.receipts=sqlite3.connect(Path(os.environ['ROOM_DATABASE']).resolve(),check_same_thread=False)
        self.receipts.row_factory=sqlite3.Row
    def originals(self,rid,after=0,before=0,limit=100000):
        with self.lock:
            from store import Store
            return [Store.original_value(r) for r in self.db.execute('''SELECT m.*,s.seq,s.hash,d.data AS summary_record FROM message_sequence s
                JOIN messages m ON m.id=s.message_id LEFT JOIN message_summaries d ON d.message_id=m.id AND d.message_hash=s.hash WHERE s.room=? AND s.seq>? AND s.seq<=?
                AND m.status IN ('complete','host_error') ORDER BY s.seq LIMIT ?''',(rid,after,before,limit))]

    def read(self,session,rid,name,args):
        with self.lock:
            row=self.db.execute('SELECT role,data FROM agent_sessions WHERE key=? AND room=?',(session,rid)).fetchone()
            if row is None:raise ValueError('此会话没有读取权限。')
            state=json.loads(row['data'])
            if name=='output_read':
                if name not in state.get('tool_names',[]):raise ValueError('此会话未启用输出回取。')
                from output_archive import read
                return read(self.receipts,self.lock,rid,session,state['branch'],args)
            if row['role']=='executor':raise ValueError('此会话没有群消息读取权限。')
            if name in ['project_read','project_search','group_list_messages'] and name not in state.get('tool_names',[]):raise ValueError('此会话未启用此只读工具。')
            if name in ['project_read','project_search']:
                from readonly_project import execute
                root=self.db.execute('SELECT project FROM rooms WHERE id=?',(rid,)).fetchone()[0]
                return execute(root,name,args)
            if name=='evidence_read':
                if row['role']!='supervisor':raise ValueError('只有监工可读取证据账本。')
                wf=self.db.execute('SELECT data FROM workflow_state WHERE room=?',(rid,)).fetchone()
                record=next((e for e in (json.loads(wf[0]) if wf else {}).get('evidence',[]) if e['id']==args.get('id')),None)
                if record is None:raise ValueError('证据不存在。')
                return record
            result=read_originals(self,rid,state['active_boundary'],name,args)
            receipt(self.receipts,session,state['branch'],result);return result


def main():
    archive=Archive();rid=os.environ['ROOM_ID'];session=os.environ['ROOM_SESSION']
    mcp=FastMCP('room')
    session_info=archive.db.execute('SELECT role,data FROM agent_sessions WHERE key=? AND room=?',(session,rid)).fetchone()
    enabled=json.loads(session_info['data']).get('tool_names',[]) if session_info else []
    if session_info and session_info['role']!='executor' and not enabled:
        enabled=['group_read_messages','group_get_message','group_search_messages']+(['evidence_read'] if session_info['role']=='supervisor' else [])
    def read(name,args):
        return archive.read(session,rid,name,args)
    def group_read_messages(after_seq:int=0,limit:int=4):return read('group_read_messages',{'after_seq':after_seq,'limit':limit})
    def group_get_message(message_id:str,offset:int=0):return read('group_get_message',{'message_id':message_id,'offset':offset})
    def group_search_messages(query:str,after_seq:int=0):return read('group_search_messages',{'query':query,'after_seq':after_seq})
    def evidence_read(id:str):return read('evidence_read',{'id':id})
    for fn in [group_read_messages,group_get_message,group_search_messages,evidence_read]:
        if fn.__name__ in enabled:mcp.add_tool(fn)
    if 'group_list_messages' in enabled:
        @mcp.tool()
        def group_list_messages(after_seq:int=0,before_seq:int=0,limit:int=20):return read('group_list_messages',{'after_seq':after_seq,'before_seq':before_seq,'limit':limit})
    if 'project_read' in enabled:
        @mcp.tool()
        def project_read(path:str,offset:int=0,limit:int=200):return read('project_read',{'path':path,'offset':offset,'limit':limit})
        @mcp.tool()
        def project_search(query:str,path:str='.',limit:int=40):return read('project_search',{'query':query,'path':path,'limit':limit})
    if 'output_read' in enabled:
        @mcp.tool()
        def output_read(id:str,offset:int=0,limit:int=8000):return read('output_read',{'id':id,'offset':offset,'limit':limit})
    mcp.run(transport='stdio')


if __name__=='__main__':main()
