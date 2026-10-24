"""Compact interval coverage and exact segment receipts, shared with MCP readers."""


def intervals(seqs):
    result=[]
    for seq in sorted(set(seqs)):
        if result and seq<=result[-1][1]+1:result[-1][1]=seq
        else:result.append([seq,seq])
    return result


def contains(ranges,seq):return any(lo<=seq<=hi for lo,hi in ranges)


def union(ranges,seqs):
    merged=[]
    for lo,hi in sorted([*ranges,*[[s,s] for s in seqs]]):
        if merged and lo<=merged[-1][1]+1:merged[-1][1]=max(merged[-1][1],hi)
        else:merged.append([lo,hi])
    return merged


def receipt(db,key,branch,result):
    for item in result.get('entries',[]):
        if 'seq' in item and item.get('feed') in ['menu','id']:
            db.execute('INSERT OR IGNORE INTO menu_deliveries VALUES(?,?,?,?)',(key,branch,item['seq'],item['feed']))
    blocks=result.get('messages',[result])
    for block in blocks:
        if 'seq' not in block:continue
        start=block.get('offset',0);end=start+len(block.get('text',''));total=block.get('total_characters',end)
        db.execute('INSERT OR IGNORE INTO tool_deliveries VALUES(?,?,?,?,?,?)',(key,branch,block['seq'],start,end,total))
    db.commit()


def received(db,key,branch):
    grouped={}
    for row in db.execute('SELECT message_seq,start,end,total FROM tool_deliveries WHERE session_key=? AND branch=? ORDER BY message_seq,start',(key,branch)):
        grouped.setdefault(row[0],[]).append(tuple(row[1:]))
    complete=[]
    for seq,parts in grouped.items():
        end=0
        for start,stop,total in parts:
            if start>end:break
            end=max(end,stop)
        if end>=max(p[2] for p in parts):complete.append(seq)
    return complete
