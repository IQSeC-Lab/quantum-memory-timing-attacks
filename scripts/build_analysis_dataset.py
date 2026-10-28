"""Extract independent statistical units for the small, analysis-only release.

The full compact-record checkers can export the four fresh-study arrays with
QMT_EXPORT_UNITS=1. This script adds historical campaign/stream units. Original
full archives remain available separately; this is explicitly a reduced view.
"""
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import zipfile
import numpy as np

root=Path(__file__).resolve().parents[1]
data=root/'data/archives';out=root/'data/analysis';out.mkdir(exist_ok=True)

def archive(pattern): return zipfile.ZipFile(next(data.glob(pattern)))
def header(z,name):
    return json.loads(z.read(next(n for n in z.namelist() if n.count('/')==1 and n.endswith('/'+name))))
def rows(z,stage):
    prefix=next(n.rsplit('/',1)[0]+'/' for n in z.namelist() if n.count('/')==1 and n.endswith('/COMPLETE.json'))+stage+'/'
    committed=[n for n in z.namelist() if n.startswith(prefix) and n.endswith('/completed.json')]
    if committed:
        for name in sorted(committed):
            marker=json.loads(z.read(name));payload=z.read(name.rsplit('/',1)[0]+'/'+marker['compact_file'])
            assert hashlib.sha256(payload).hexdigest()==marker['compact_sha256']
            if marker['compact_file'].endswith('.gz'):
                for line in gzip.decompress(payload).splitlines():
                    if line.strip():yield json.loads(line)
            else:
                yield json.loads(payload)
        return
    for name in sorted(z.namelist()):
        if not name.startswith(prefix):continue
        if name.endswith('.compact.jsonl.gz'):
            for line in gzip.decompress(z.read(name)).splitlines():
                if line.strip():yield json.loads(line)
        elif name.endswith('.compact.json'):yield json.loads(z.read(name))
def save(name,units,reference,**extra):
    np.savez_compressed(out/(name+'.npz'),units=np.asarray(units),
                        reference=np.asarray(json.dumps(reference)),**extra)

with archive('*Q12*zip') as z:
    ref=header(z,'analysis.json');proto=header(z,'protocol.json');groups=defaultdict(lambda:[0,0])
    for r in rows(z,'evaluation'):
        s=r['spec'];g=groups[s['workload'],s['scenario'],s['campaign'],s['arm']]
        g[0]+=r['wrong_output'];g[1]+=1
    comparisons=ref['primary_contrasts']+ref['secondary_contrasts'];units=[]
    for c in comparisons:
        x=[]
        for campaign in range(32):
            a,b=[groups[c['workload'],c['scenario'],campaign,arm] for arm in ['learned','random_phase']]
            assert a[1]==b[1]==c['shots_per_arm_per_campaign']
            x.append((a[0]-b[0])/a[1])
        units.append(x)
    own=np.full((2,32,16,256),-1,dtype=np.int8)
    for r in rows(z,'calibration'):
        o=r['own_observation'];candidate=o['candidate']
        if not candidate.startswith('phase_'):continue
        key=(['plus','teleport'].index(o['workload']),o['campaign'],int(candidate[-2:]),o['shot'])
        assert own[key]==-1;own[key]=o['returned_bit']
    assert (own>=0).all()
    save('q12',units,ref,seed=np.asarray(proto['bootstrap_root']),own_bits=own,
         selections=np.asarray(json.dumps(header(z,'frozen_selections.json'))))

with archive('*Q14*zip') as z:
    ref=header(z,'analysis.json');units=[]
    for c in ref['primary_contrasts']:
        group=sorted([r for r in ref['campaign_differences'] if (r['workload'],r['profile'])==(c['workload'],c['profile'])],key=lambda r:r['campaign'])
        assert len(group)==32
        units.append([[r[k] for r in group] for k in ['delta_infidelity','delta_wrong_output','delta_wrong_or_unfinished','delta_storage_ms']])
    save('q14',units,ref,seed=np.asarray(header(z,'protocol.json')['bootstrap_root']))

with zipfile.ZipFile(data/'E1_Review_complete_safe_resume.zip') as z:
    ref=json.loads(z.read('analysis.json'));proto=json.loads(z.read('protocol.json'));groups=defaultdict(lambda:[0,0])
    for name in sorted(z.namelist()):
        if not name.startswith('evaluation/') or not name.endswith('.compact.jsonl.gz'):continue
        for line in gzip.decompress(z.read(name)).splitlines():
            r=json.loads(line);g=groups[r['e1_task']['campaign'],r['e1_policy']];g[0]+=r['wrong_output'];g[1]+=1
    units=[]
    for c in ref['contrasts']:
        other=c['contrast'].split(' minus ')[1];x=[]
        for campaign in range(32):
            a,b=[groups[campaign,arm] for arm in ['q12_full256',other]]
            assert a[1]==b[1]==256;x.append((a[0]-b[0])/256)
        units.append(x)
    save('e1',units,ref,seed=np.asarray(proto['bootstrap_root']),
         policies=np.asarray(json.dumps(json.loads(z.read('frozen_policies.json')))))

with archive('P1_RQ13*zip') as z:
    ref=header(z,'application_summary.json');groups=defaultdict(dict)
    for r in rows(z,'evaluation'):
        s=r['spec']
        if s['policy']!='QUOTA_FCFS' or s['arm'] not in ['selected','random']:continue
        groups[s['profile'],s['cost_ns'],s['variant'],s['arm']][s['stream']]=sum(o['infidelity'] for o in r['outcomes'])/12
    units=[];seeds=[]
    for c in ref['primary']:
        key=(c['profile'],c['cost_ns'],c['variant']);a,b=[groups[key+(arm,)] for arm in ['selected','random']]
        assert set(a)==set(b) and len(a)==32
        units.append([a[i]-b[i] for i in sorted(a)])
        seed_key=[*key,'QUOTA_FCFS','selected','primary']
        seeds.append(int(hashlib.sha256(json.dumps(seed_key,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:8],16))
    save('application_earlier',units,ref,seeds=np.asarray(seeds,dtype=np.uint64))

with archive('q16-fresh-planning-compact.zip') as z:
    ref=header(z,'planning_summary.json');units=[];keys=[]
    for r in rows(z,'planning'):
        counts=[0,0,0,0]
        for q in r['request_rows']:
            if not q['measured']:continue
            i=3 if q['finished_ns'] is None else 2 if q['response_ns']>5_000_000 else 1 if q['wrong_output'] else 0
            counts[i]+=1
        assert sum(counts)==256
        units.append(counts);s=r['spec'];keys.append([s['arrival'],s['arm'],s['stream']])
    assert len(units)==192
    save('q16',np.asarray(units,dtype=np.uint16),ref,keys=np.asarray(json.dumps(keys)))

manifest={'scope':'Reduced analysis dataset: independent paired endpoint differences, calibration bits, and public-clock stream counts. Not full compact records or checkpoint receipts.',
          'source_archives':json.loads((root/'data/MANIFEST.json').read_text())['files'],'files':[]}
for p in sorted(out.glob('*.npz')):
    manifest['files'].append({'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(root/'data/ANALYSIS_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Analysis dataset:',sum(x['bytes'] for x in manifest['files']),'bytes')
