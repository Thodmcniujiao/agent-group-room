"""Export an allowlist of public reports; never bundle native session directories."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

PATTERNS=('README.md','approved-plan.md','*.png','ui-verification.json','live-preflight*.json','executor-state-acl.json',
    'launch-status.json','deployment.json','verification.json','*/unit-tests.json','*/unit-tests.log',
    'paired-supervisor/comparison.json','claude-live/result.json','claude-snapshot/result.json','acceptance/live-task*.json')


def export(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve();files={}
    for pattern in PATTERNS:
        for path in source.glob(pattern):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):continue
            relative=path.relative_to(source)
            if any(p in ['state','live-state','sessions','credentials'] for p in relative.parts):continue
            files[relative.as_posix()]=path
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,path in sorted(files.items()):archive.write(path,name)
        manifest={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sorted(files.items())}
        archive.writestr('export-manifest.json',json.dumps({'policy':'explicit public-report allowlist; native states excluded','sha256':manifest},indent=2))
    return list(sorted(files))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('destination');args=parser.parse_args()
    print(json.dumps({'files':export(args.source,args.destination)},ensure_ascii=False))
