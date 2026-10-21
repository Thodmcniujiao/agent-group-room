"""Reproducible offline regression archive. Live calls are a separate opt-in."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]


class Result(unittest.TextTestResult):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.cases=[]
    def addSuccess(self,test):super().addSuccess(test);self.cases.append({'test':test.id(),'status':'passed'})
    def addFailure(self,test,err):super().addFailure(test,err);self.cases.append({'test':test.id(),'status':'failed'})
    def addError(self,test,err):super().addError(test,err);self.cases.append({'test':test.id(),'status':'error'})
    def addSkip(self,test,reason):super().addSkip(test,reason);self.cases.append({'test':test.id(),'status':'skipped','reason':reason})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',default='test-reports/context-2026-10-21')
    args=parser.parse_args();output=(ROOT/args.report_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    logging.disable(logging.CRITICAL);sys.path[:0]=[str(ROOT/'gui')]
    suite=unittest.TestSuite([unittest.defaultTestLoader.discover(str(ROOT/'gui'),pattern='test_*.py'),
        unittest.defaultTestLoader.discover(str(ROOT/'scripts'),pattern='test_deployment.py',top_level_dir=str(ROOT/'scripts'))])
    try:
        with (output/'unit-tests.log').open('w',encoding='utf-8') as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    finally:
        if 'server' in sys.modules:sys.modules['server'].STORE.db.close()
    files=[p for folder in ['gui','scripts','plugins/tiered-dev/scripts'] for p in (ROOT/folder).rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and p.suffix in ['.py','.js','.html','.css']]
    manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True).stdout.strip()
    report={'timestamp_utc':datetime.now(timezone.utc).isoformat(),'git_head':head,'working_tree':'contains local changes; hashes identify the tested sources',
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'passed':result.wasSuccessful(),'cases':result.cases,'source_sha256':manifest,
        'live_model_calls':False}
    (output/'unit-tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['tests_run','failures','errors','skipped','passed']},ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
