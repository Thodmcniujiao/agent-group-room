"""Read an existing A1 database without constructing Store or changing room state."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'gui'))
from usage_report import report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=(Path(os.environ['TIERED_DATA_HOME']) if os.environ.get('TIERED_DATA_HOME') else Path.home()/'.local/share/tiered-dev')/'gui/rooms.sqlite3')
    parser.add_argument('--room');parser.add_argument('--by',choices=['member','role','session','day','adapter','kind'],default='member')
    parser.add_argument('--timezone',default='Europe/London');parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(row) for row in db.execute('SELECT * FROM usage_records'+(' WHERE room=?' if args.room else '')+' ORDER BY id',(args.room,) if args.room else ())]
    print(json.dumps(report(rows,args.by,args.timezone),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
