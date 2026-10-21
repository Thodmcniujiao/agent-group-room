"""Read-only MCP projection; no Store construction or database mutations."""
import json
import os
import sqlite3
import threading
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from context import read_originals


class Archive:
    def __init__(self):
        self.db=sqlite3.connect(Path(os.environ['ROOM_DATABASE']).resolve().as_uri()+'?mode=ro',uri=True,check_same_thread=False)
        self.db.row_factory=sqlite3.Row;self.lock=threading.RLock()
    def originals(self,rid,after=0,before=0,limit=100000):
        with self.lock:
            return [dict(r) for r in self.db.execute('''SELECT m.*,s.seq,s.hash FROM message_sequence s
                JOIN messages m ON m.id=s.message_id WHERE s.room=? AND s.seq>? AND s.seq<=?
                AND m.status='complete' ORDER BY s.seq LIMIT ?''',(rid,after,before,limit))]


def main():
    archive=Archive();rid=os.environ['ROOM_ID'];session=os.environ['ROOM_SESSION']
    mcp=FastMCP('room')
    def read(name,args):
        row=archive.db.execute('SELECT role,data FROM agent_sessions WHERE key=? AND room=?',(session,rid)).fetchone()
        if row is None or row['role']=='executor':raise ValueError('此会话没有群消息读取权限。')
        state=json.loads(row['data']);boundary=state['active_boundary']
        return read_originals(archive,rid,boundary,name,args)
    @mcp.tool()
    def group_read_messages(after_seq:int=0,limit:int=4):return read('group_read_messages',{'after_seq':after_seq,'limit':limit})
    @mcp.tool()
    def group_get_message(message_id:str,offset:int=0):return read('group_get_message',{'message_id':message_id,'offset':offset})
    @mcp.tool()
    def group_search_messages(query:str,after_seq:int=0):return read('group_search_messages',{'query':query,'after_seq':after_seq})
    mcp.run(transport='stdio')


if __name__=='__main__':main()
