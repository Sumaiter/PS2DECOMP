"""Optional isolated VS Code integration check using synthetic ELF test data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.test_project_engine import ProjectTests


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--locale',choices=['en','de'],default='en');args=ap.parse_args()
    fixture=ProjectTests(methodName='runTest');fixture.setUp()
    try:
        settings=fixture.root/'.vscode';settings.mkdir()
        (settings/'settings.json').write_text(json.dumps({'ps2decomp.python':sys.executable}))
        cli=Path(shutil.which('code') or '')
        executable=cli.parent.parent/'Code.exe' if os.name=='nt' else cli
        directory=ROOT/'build'/('host-'+args.locale);directory.mkdir(parents=True,exist_ok=True)
        report=ROOT/'build'/('vscode-'+args.locale+'.json')
        report.unlink(missing_ok=True)
        command=[str(executable),'--new-window','--wait','--disable-gpu','--skip-welcome','--skip-release-notes',
            '--disable-workspace-trust','--user-data-dir',str(directory/'user'),
            '--extensions-dir',str(directory/'extensions'),'--locale',args.locale,
            '--extensionDevelopmentPath='+str(ROOT),'--extensionTestsPath='+str(ROOT/'tests/vscode_host.js'),str(fixture.root)]
        result=subprocess.run(command,env={**os.environ,'PS2DECOMP_TEST_LOCALE':args.locale},timeout=90)
        if not report.is_file():raise SystemExit('VS Code did not produce a test report; exit '+str(result.returncode))
        print(report.read_text());raise SystemExit(0 if json.loads(report.read_text()).get('passed') else 1)
    finally:
        fixture.temp.cleanup()


if __name__=='__main__':main()
