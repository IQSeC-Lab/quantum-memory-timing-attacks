"""Upstream translation/parser compatibility check, NOT an effect comparison.

    Never edits compiler output to make the old runtime accept it. No installs.
    Source fixtures are exact MIT-licensed upstream tests, pinned by manifest.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def gate(folder,tools_dir=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'upstream/MANIFEST.json').read_text())
    for item in manifest['fixtures']:
        if sha(ROOT/'upstream'/item['local'])!=item['sha256']:raise ValueError('Upstream fixture changed')
    names=('qoala-opt','qoala-translate','FileCheck')
    found={n:(str(Path(tools_dir)/n) if tools_dir and (Path(tools_dir)/n).is_file() else shutil.which(n)) for n in names}
    out=dict(upstream=manifest,tools=found,fixtures=[],native_effect_comparison=False,
             status='BLOCKED_MISSING_COMPILER',required_next='Compile the actual matched application, verify noiseless semantics and execute common traffic before claiming an upstream baseline.')
    if not found['qoala-translate']:
        (folder/'COMPILER_GATE.json').write_text(json.dumps(out,indent=2)+'\n');return out
    out['binary_sha256']={n:sha(p) for n,p in found.items() if p}
    out['provenance']='Binary hashes recorded; binary-to-upstream-commit build provenance remains to be supplied.'
    for n,p in found.items():
        if p:
            r=subprocess.run([p,'--help'],capture_output=True,text=True,timeout=60)
            (folder/(n+'-help.txt')).write_text(r.stdout+r.stderr)
    for item in manifest['fixtures']:
        src=ROOT/'upstream'/item['local'];tag=src.stem;cmd=[found['qoala-translate'],str(src),'--mlir-to-iqoala']
        started=time.perf_counter();r=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
        target=folder/(tag+'.iqoala');target.write_text(r.stdout)
        (folder/(tag+'.stderr.txt')).write_text(r.stderr)
        row=dict(source=item['source'],source_sha256=item['sha256'],command=cmd,returncode=r.returncode,
                 wall_seconds=time.perf_counter()-started,output_sha256=sha(target),parse_status='NOT_ATTEMPTED')
        if r.returncode==0:
            if found['FileCheck']:
                check=subprocess.run([found['FileCheck'],str(src)],input=r.stdout,capture_output=True,text=True,timeout=60)
                row['filecheck_returncode']=check.returncode
                (folder/(tag+'.filecheck.txt')).write_text(check.stdout+check.stderr)
            try:
                from qoala.lang.parse import QoalaParser
                program=QoalaParser(r.stdout).parse()
                row.update(parse_status='PASS_PARSE_ONLY',blocks=len(program.blocks))
            except Exception as exc:row.update(parse_status='BLOCKED',parser_error=repr(exc))
        out['fixtures'].append(row)
    good=all(r['returncode']==0 and r.get('filecheck_returncode',0)==0 and r['parse_status']=='PASS_PARSE_ONLY' for r in out['fixtures'])
    out['status']='PASS_TRANSLATION_PARSE_ONLY' if good else 'COMPATIBILITY_BLOCKED'
    out['upstream_full_test_suite']='NOT_RUN'
    out['native_semantic_validation']='NOT_RUN'
    out['runtime_mutated']=False
    (folder/'COMPILER_GATE.json').write_text(json.dumps(out,indent=2)+'\n');return out

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--tools-dir');a=p.parse_args()
    r=gate(a.folder,a.tools_dir);print(json.dumps(r,indent=2))
    # This is an information-collecting gate. Missing optional compiler tools
    # do not silently pass and do not prevent running unrelated diagnostics.
    sys.exit(0 if r['status']=='PASS_TRANSLATION_PARSE_ONLY' else 2)
