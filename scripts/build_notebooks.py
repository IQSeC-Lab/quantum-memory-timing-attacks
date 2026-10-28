"""Create indexed notebook front ends to the retained experiment implementations."""
import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def cell(kind, text):
    c = {'cell_type': kind, 'metadata': {}, 'source': text.splitlines(keepends=True)}
    if kind == 'code': c.update(execution_count=None, outputs=[])
    return c

setup = '''from pathlib import Path
import os, subprocess, sys

ROOT = next((p for p in (Path.cwd(), *Path.cwd().parents)
             if (p / "scripts/reproduce.py").is_file()), None)
if ROOT is None:
    raise RuntimeError("Open this notebook from within the extracted repository")
def run_script(script, *arguments):
    subprocess.run([sys.executable, str(ROOT / script), *map(str, arguments)],
                   cwd=ROOT, check=True)
print("Repository:", ROOT)
'''

studies = [
 ('01_RQ1_Campaign_Timing', 'RQ1 — Original campaign timing', ['scripts/reproduce.py','q12'],
  'experiments/historical/q12/source/q12.py', ['run','--repo','{repo}','--run-dir','{root}/runs/q12','--mode','{mode}','--workers','{workers}'], 'pilot', [],
  '32 campaigns per workload; 98,304 evaluation executions. Campaigns are independent units. Teleportation increases over random timing; the plus-state result remains unresolved.'),
 ('02_RQ1_Calibration_Cost', 'RQ1 — Calibration cost and simpler policies', ['scripts/reproduce.py','e1'],
  'experiments/historical/calibration_cost/source/p1_e1_resume.py', ['--project','{root}/runs','--q12-run','{q12_run}','--q12-code','{root}/experiments/historical/q12/source','--repo','{repo}','--run-dir','{root}/runs/e1','--mode','{mode}','--workers','{workers}'], 'pilot', ['q12_run'],
  'Nine policies; 73,728 evaluation trials plus a separate 1,152-trial pilot. Two primary contrasts. Do not pool pilot outcomes or replace the original bootstrap method.'),
 ('03_RQ1_Timing_Grid', 'RQ1 — Fresh timing grid at equal competing work', ['scripts/reproduce.py','timing'],
  'execution/main_recovery/recover_native.py', ['run','--package','{root}/experiments/P1_Feedback_Closure','--results','{root}/runs/main','--family','timing','--mode','{mode}','--workers','{workers}','--repo','{repo}'], 'smoke', [],
  '20 arms, 16,384 paired trials per workload, 655,360 executions, 36 simultaneous contrasts. The best fixed grid value is an evaluation oracle.'),
 ('04_RQ2_Memory_Transfer', 'RQ2 — Frozen-policy transfer across memory models', ['scripts/reproduce.py','q14'],
  'experiments/historical/q14/source/q14.py', ['run','--repo','{repo}','--run-dir','{root}/runs/q14','--mode','{mode}','--workers','{workers}','--q12-run','{q12_run}','--q13-run','{q13_run}','--q12-audit','{q12_audit}','--q13-audit','{q13_audit}'], 'pilot', ['q12_run','q13_run','q12_audit','q13_audit'],
  '98,304 executions. Reuses frozen phases without retraining. Native execution requires exact predecessor runs and completed raw-audit identities, including original path bindings.'),
 ('05_RQ3_Preparation_and_Storage', 'RQ3 — Preparation placement and recorded storage', ['scripts/reproduce.py','application'],
  'execution/main_recovery/recover_native.py', ['run','--package','{root}/experiments/P1_Feedback_Closure','--results','{root}/runs/main','--family','application','--mode','{mode}','--workers','{workers}','--repo','{repo}'], 'smoke', [],
  'Uses the same primary application study as notebook 06. Run native application simulations once, then reuse their results for both RQs. Storage displays are descriptive; the primary timing interval includes zero.'),
 ('06_RQ4_Quality_and_Deadlines', 'RQ4 — Scheduling protection, quality, and deadlines', ['scripts/reproduce.py','application'],
  'execution/main_recovery/recover_native.py', ['run','--package','{root}/experiments/P1_Feedback_Closure','--results','{root}/runs/main','--family','application','--mode','{mode}','--workers','{workers}','--repo','{repo}'], 'smoke', [],
  'Shares the 8,192-stream, 655,360-execution primary application study with notebook 05. Six primary contrasts. Keep all offered requests in the denominator and compare EDF with priority and exclusion.'),
 ('07_Noise_Controls', 'Noise controls — Ideal, memory-only, gate-only, and combined', ['scripts/reproduce.py','diagnostics'],
  'execution/diagnostic_recovery/recover_additional.py', ['run','--package','{root}/experiments/P1_Additional_Studies','--results','{root}/runs/additional','--family','causal','--mode','{mode}','--workers','{workers}','--repo','{repo}'], 'smoke', [],
  '512 streams, 61,440 executions, 140 simultaneous contrasts. No interval excludes zero. The analysis command also verifies the companion sensitivity family.'),
 ('08_Sensitivity', 'Sensitivity — Fifteen one-factor configurations', ['scripts/reproduce.py','diagnostics'],
  'execution/diagnostic_recovery/recover_additional.py', ['run','--package','{root}/experiments/P1_Additional_Studies','--results','{root}/runs/additional','--family','robustness','--mode','{mode}','--workers','{workers}','--repo','{repo}'], 'smoke', [],
  '256 streams, 115,200 executions, 450 simultaneous contrasts. Point estimates change sign; none of these intervals excludes zero.'),
 ('09_Public_Clock', 'Public-clock interference — Weaker timing access', ['scripts/reproduce.py','q16'],
  'experiments/historical/q16/source/q16_fresh.py', ['run','--repo','{repo}','--run-dir','{root}/runs/q16-fresh','--calibration-dir','{calibration_dir}','--workers','{workers}'], None, ['calibration_dir'],
  '192 simulations, 49,152 measured requests. The retained descriptive operating point misses the joint service criterion. Native execution requires its bound calibration directory.'),
 ('10_Earlier_Application', 'Earlier application factorial study', ['scripts/reproduce.py','application_earlier'],
  'experiments/historical/application_earlier/source/app_cli.py', ['run','--repo','{repo}','--run-dir','{root}/runs/application-earlier','--mode','{mode}','--workers','{workers}'], 'pilot', [],
  '7,680 simulations; 32 streams per condition. Keep this historical study separate from the fresh 8,192-stream primary application study.'),
 ('11_Predecessor_Memory_Models', 'Predecessor Q13 — Memory-model prerequisite', None,
  'experiments/historical/q13/source/q13.py', ['run','--repo','{repo}','--run-dir','{root}/runs/q13','--mode','{mode}','--workers','{workers}'], 'pilot', [],
  'Supporting predecessor implementation for Q14; it is not an additional RQ or a substitute for the frozen-policy transfer evaluation. Its native raw audit is a separate prerequisite.'),
 ('12_Public_Clock_Calibration', 'Public-clock calibration — Retained predecessor', None,
  'experiments/historical/q16_calibration/source/q16_calibration.py', ['run','--repo','{repo}','--run-dir','{root}/runs/q16-calibration','--workers','{workers}'], None, [],
  'Retained calibration implementation for the public-clock pipeline. Do not retune its selected policy after inspecting the planning result. This native command has no pilot-mode argument.'),
 ('13_Raw_Replay', 'Completed raw replay — Receipts and exact-source qualification', ['scripts/reproduce.py','receipts'],
  'audit/raw_replay/audit_raw.py', ['run','--main-root','{main_root}','--additional-root','{additional_root}','--output','{root}/runs/new-raw-audit','--mode','{mode}','--workers','{workers}'], 'sample', ['main_root','additional_root'],
  '11,008 retained completed receipts cover 1,487,360 records. Native raw replay needs the original raw files. The recovered auditor identity differs from the completed server receipt identity; resolve docs/RELEASE_STATUS.md before claiming exact executed-source provenance. A sample replay is not full coverage.'),
]

out = ROOT / 'notebooks'
out.mkdir(exist_ok=True)
catalog = []
for name, title, analysis, native, arguments, mode, prerequisites, note in studies:
    config = f'RUN_NATIVE = False\nWORKERS = 4\nMODE = {mode!r}\nQOALA_REPO = os.environ.get("QOALA_REPO")\n'
    for key in prerequisites: config += f'{key.upper()} = None  # Absolute path to matching original evidence\n'
    values = {'root':'str(ROOT)', 'repo':'QOALA_REPO', 'workers':'str(WORKERS)', 'mode':'MODE'}
    values.update({k:k.upper() for k in prerequisites})
    command = '[\n    '+',\n    '.join(('str(ROOT / '+repr(native)+')', *[('str(ROOT / '+repr(x[len('{root}/'):])+')') if x.startswith('{root}/') else values[x[1:-1]] if x.startswith('{') and x.endswith('}') else repr(x) for x in arguments]))+'\n]'
    required = (['QOALA_REPO'] if '{repo}' in arguments else []) + [x.upper() for x in prerequisites]
    native_code = 'if RUN_NATIVE:\n'
    for key in required: native_code += f'    if not {key}: raise ValueError("Set {key} before native execution")\n'
    native_code += '    command = [sys.executable, *'+command.replace('\n','\n    ')+']\n    subprocess.run(command, cwd=ROOT, check=True)\nelse:\n    print("Native execution disabled; retained-result analysis remains available.")\n'
    cells = [cell('markdown', f'# {title}\n\n{note}\n\nThis is a curated notebook front end. The scientific implementation remains in the frozen source modules; this notebook does not invent a replacement experiment. See the root README for environment, pairing, statistics, and resource requirements.\n'),cell('code',setup),cell('markdown','## Verify the small statistical dataset\nThe nine NumPy files retain independent statistical units, not full request records or checkpoint receipts. Full-record checks remain separately available with --full-records.\n'),cell('code','run_script("scripts/check_data.py")\n')]
    if analysis: cells += [cell('markdown','## Reproduce the retained analysis\nNo native quantum simulator is required for this step.\n'),cell('code','run_script('+', '.join(repr(x) for x in analysis)+')\n')]
    else: cells += [cell('markdown','## Supporting prerequisite\nThis notebook exposes a supporting native implementation. It is not counted as a newly validated paper comparison. Its original source and retained metadata are included.\n')]
    if name.startswith(('05_','06_')): cells += [cell('code','run_script("scripts/reproduce.py", "figures")\n')]
    cells += [cell('markdown','## Native execution configuration\nUses the existing pinned simulator environment. The default does not launch simulations. For historical runs, supply the exact predecessor evidence required by the original source checks.\n'),cell('code',config),cell('code',native_code)]
    obj = {'cells':cells,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python'}},'nbformat':4,'nbformat_minor':5}
    for i,c in enumerate(cells): c['id'] = f'cell-{i:02d}'
    path = out/(name+'.ipynb');path.write_text(json.dumps(obj,indent=1)+'\n')
    catalog.append({'notebook':str(path.relative_to(ROOT)),'title':title,'origin':'curated front end','analysis_command':analysis,'native_source':native,'default_native_execution':False,'native_prerequisites':prerequisites})

cells = [cell('markdown','# Reproduce all retained results\n\nStart here. This checks the small statistical dataset, recomputes the paired trial/stream and historical campaign comparisons, runs engineering checks, and regenerates figure/table data. It does not repeat full compact-record or raw-trace integrity checks. No native quantum simulation is launched.\n'),cell('code',setup),cell('code','run_script("scripts/check_data.py")\nrun_script("scripts/reproduce.py", "all")\n')]
for i,c in enumerate(cells): c['id']=f'cell-{i:02d}'
(out/'00_Start_Here.ipynb').write_text(json.dumps({'cells':cells,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python'}},'nbformat':4,'nbformat_minor':5},indent=1)+'\n')
originals = [{'path':str(p.relative_to(ROOT)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted((out/'originals').glob('*.ipynb'))]
(ROOT/'docs/NOTEBOOK_COVERAGE.json').write_text(json.dumps({'start_here':'notebooks/00_Start_Here.ipynb','studies':catalog,'recovered_originals':originals},indent=2)+'\n')
index = ['# Notebook guide','','Start with [00_Start_Here.ipynb](00_Start_Here.ipynb). Native execution is disabled by default.','','| Notebook | Experiment | Implementation |','|---|---|---|']
for row in catalog:index.append(f'| [{Path(row["notebook"]).name}]({Path(row["notebook"]).name}) | {row["title"]} | `{row["native_source"]}` |')
index += ['','The numbered notebooks are newly curated entry points to the retained implementations. Original server notebooks are preserved separately under `originals/` and inside frozen packages. RQ3 and RQ4 share one native application study; do not rerun it solely because two notebooks reference it.']
index += ['', '## Recovered original notebooks', '']
index += [f'- [{Path(x["path"]).name}](originals/{Path(x["path"]).name})' for x in originals]
(out/'README.md').write_text('\n'.join(index)+'\n')
print('Created 14 indexed notebooks and a machine-readable coverage map.')
