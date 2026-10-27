"""Acknowledge only committed writes independently verified against execution evidence."""
import hashlib
import json
from pathlib import Path


def verified_records(root,records,allowed_paths,expected_hashes):
    root=Path(root).resolve();allowed={(root/name).resolve() for name in allowed_paths};verified=[];seen=set()
    for record in records:
        name=record.get('path');transaction=record.get('transaction_id')
        if not isinstance(name,str) or not isinstance(transaction,str) or not transaction or transaction in seen:continue
        seen.add(transaction)
        target=(root/name).resolve()
        expected=expected_hashes.get(name)
        if record.get('status')!='committed' or record.get('warnings') or not expected or expected!=record.get('sha256'):continue
        if not target.is_relative_to(root) or target not in allowed or not target.is_file():continue
        if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:continue
        verified.append(record)
    return verified


async def reconcile(session,root,records,allowed_paths,expected_hashes,decode):
    eligible=verified_records(root,records,allowed_paths,expected_hashes)
    if not eligible:return records,[]
    result=json.loads(decode(await session.call_tool('acknowledge_deepseek_mutations',{'transaction_ids':[r['transaction_id'] for r in eligible]})))
    if not result.get('ok'):raise RuntimeError('已核实写入的恢复确认失败，记录已保留。')
    fresh=json.loads(decode(await session.call_tool('get_deepseek_recovery',{})))
    if not fresh.get('ok'):raise RuntimeError('恢复确认后未能重新读取状态，请复查；未重新派发执行。')
    remaining=fresh.get('pending',[]);remaining_ids={r.get('transaction_id') for r in remaining}
    confirmed=[r for r in eligible if r['transaction_id'] not in remaining_ids]
    return remaining,confirmed
