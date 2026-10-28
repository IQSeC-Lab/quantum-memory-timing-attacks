#!/usr/bin/env python3
import zipfile,hashlib,collections,json,gzip,io,math,sys,time,statistics
from pathlib import Path
import numpy as np
p=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path('E1_Review_complete_safe_resume.zip').resolve()

def sha(raw): return hashlib.sha256(raw).hexdigest()
def canon(ob): return json.dumps(ob,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()
def digest(ob): return sha(canon(ob))
issues=[]; stage_rows={}; stage_stat={}; report={};
with zipfile.ZipFile(p) as z:
 files=set(z.namelist())
 def content(name):
  if name not in files: raise AssertionError('Missing: '+name)
  return z.read(name)
 def js(name):return json.loads(content(name))
 for name in ['source/p1_e1.py','protocol.json','analysis.json','frozen_policies.json','evaluation/manifest.json','pilot/manifest.json']:
  print(name,sha(content(name)))
 protocol=js('protocol.json'); analysis=js('analysis.json'); policies=js('frozen_policies.json');complete=js('COMPLETE.json');pilotmeta=js('PILOT_PASSED.json')
 assert protocol['source_sha256']==sha(content('source/p1_e1.py'))
 assert protocol['policies_sha256']==digest(policies)
 assert analysis['policies_sha256']==sha(content('frozen_policies.json'))
 assert analysis['protocol_sha256']==sha(content('protocol.json'))
 assert complete['protocol_sha256']==sha(content('protocol.json'))
 assert complete['analysis_sha256']==sha(content('analysis.json'))
 report['identities_ok']=True
 for stage in ['pilot','evaluation']:
  manifest=js(stage+'/manifest.json');resources=js(stage+'/resources.json')
  assert resources['stage_manifest_sha256']==sha(content(stage+'/manifest.json'))
  assert resources['batches']==len(manifest)
  rows=[];batchstats=[]
  for j,m in enumerate(manifest):
   stem=f'{stage}/batch-{j:05d}/';marker=js(stem+'complete.json');job=js(stem+'job.json')
   assert m['index']==j
   assert marker['job_hash']==digest(job)
   assert m['job_hash']==digest(job)
   assert m['raw_sha256']==marker['raw_sha256']
   assert job['run_protocol_sha256']==digest(protocol)
   assert job['stage']==stage
   assert job['e1_code_hash']==protocol['source_sha256']
   assert job['q12_code_hashes']==protocol['q12']['q12_source_hashes']
   assert job['phases']=={str(c):policies[str(c)] for c in sorted({int(t['campaign']) for t in job['tasks']})}
   if marker['raw_file'] in files: raise AssertionError('Unexpected raw filename alone as archive entry')
   for suffix in ['compact','stats']:
    loc=stem+marker[suffix+'_file']
    assert sha(content(loc))==marker[suffix+'_sha256'],loc
   rec=[json.loads(line) for line in gzip.decompress(content(stem+marker['compact_file'])).splitlines() if line.strip()]
   assert len(rec)==len(job['tasks'])
   assert [r['e1_task'] for r in rec]==job['tasks']
   stats=js(stem+marker['stats_file'])
   assert stats['trials']==len(rec)
   assert m['stats']==stats
   for r,t in zip(rec,job['tasks']):
    arm=t['policy'];c=str(t['campaign'])
    assert r['e1_policy']==arm
    assert r['spec']['workload']=='teleport' and r['spec']['stage']=='evaluation'
    assert r['spec']['scenario']=='primary_0500us' and r['spec']['scheduler']=='FCFS'
    assert r['spec']['campaign']==t['campaign'] and r['spec']['shot']==t['shot']
    assert r['selected_phase_ns']==policies[c][arm]
    assert r['competitor_service_ns']==(0 if arm=='no_competitor' else 1200000)
    assert r['wrong_output'] in (0,1) and r['unfinished'] in (0,1)
   rows+=rec;batchstats.append(stats)
  assert len(rows)==resources['trials']
  for k,v in dict(worker_cpu_s=sum(s['cpu_seconds'] for s in batchstats),worker_wall_s=sum(s['wall_seconds'] for s in batchstats),max_worker_ram_mib=max(s['peak_rss_mib'] for s in batchstats)).items():
   assert math.isclose(v,resources[k],rel_tol=1e-12),k
  stage_rows[stage]=rows;stage_stat[stage]=resources
  report[stage]={'records':len(rows),'batches':len(manifest),'resources':resources,'raw_traces_in_archive':len([x for x in files if x.startswith(stage+'/') and x.endswith('.raw.jsonl.gz')])}
  print(stage,'OK:',len(rows),'rows',len(manifest),'hashed compact batches, no raw traces=',report[stage]['raw_traces_in_archive']==0)
 assert pilotmeta['pilot_resources']==stage_stat['pilot']
 assert complete['evaluation_resources']==stage_stat['evaluation']
 # Q12 protocol, fixed frozen phase 
 arms=protocol['counts']['arms'];nC=protocol['counts']['campaigns'];nS=protocol['counts']['shots_per_arm_campaign']
 evalrows=stage_rows['evaluation'];assert len(evalrows)==len(arms)*nC*nS
 groups=collections.defaultdict(dict)
 for r in evalrows:
  t=r['e1_task'];key=(t['campaign'],t['policy']); s=t['shot']
  assert s not in groups[key],(key,s)
  groups[key][s]=r
 assert len(groups)==len(arms)*nC
 for c in range(nC):
  for a in arms:assert set(groups[c,a])==set(range(nS)),(c,a)
  for s in range(nS):
   ref=groups[c,arms[0]][s]
   for a in arms[1:]:
    r=groups[c,a][s]
    assert r['state']==ref['state']
    assert r['target_request_ns']==ref['target_request_ns']
    assert r['competitor_service_ns']==(0 if a=='no_competitor' else ref['competitor_service_ns'])
 assert all(r['unfinished']==0 for r in evalrows)
 report['all_evaluation_complete']=True
 summaries=[]
 for a in arms:
  rr=[groups[c,a][s] for c in range(nC) for s in range(nS)]
  summaries.append(dict(policy=a,offered=len(rr),wrong_output=sum(r['wrong_output'] for r in rr),wrong_fraction=np.mean([r['wrong_output'] for r in rr]),incomplete=sum(r['unfinished'] for r in rr),correct_completed=sum((1-r['wrong_output'])*(1-r['unfinished']) for r in rr),mean_completed_latency_ms=float(np.mean([(r['target_finished_ns']-r['target_request_ns'])/1e6 for r in rr])),mean_storage_ms=float(np.mean([r['physical_storage_ns']/1e6 for r in rr]))))
 for a,b in zip(summaries,analysis['summaries']):
  assert a['policy']==b['policy']
  for key in a:
   assert math.isclose(a[key],b[key],abs_tol=1e-11,rel_tol=1e-11) if isinstance(a[key],(float,int)) else a[key]==b[key],(a['policy'],key,a[key],b[key])
 report['summaries_reproduced']=True
 rng=np.random.default_rng(protocol['bootstrap_root']);boots=rng.integers(0,nC,size=(20000,nC))
 comparisons=[]
 for a in arms[1:]:
  d=np.array([sum(groups[c,'q12_full256'][s]['wrong_output']-groups[c,a][s]['wrong_output'] for s in range(nS))/nS for c in range(nC)])
  primary=a in protocol['primary_comparators'];tail=.05/(2*(len(protocol['primary_comparators']) if primary else 1))
  draws=d[boots].mean(axis=1)
  o=dict(contrast='q12_full256 minus '+a,primary=primary,campaign_mean_difference_pp=100*float(d.mean()),interval_pp=(100*np.quantile(draws,[tail,1-tail])).tolist(),positive_campaigns=int((d>0).sum()),negative_campaigns=int((d<0).sum()),tied_campaigns=int((d==0).sum()))
  counterpart=next(x for x in analysis['contrasts'] if x['contrast']==o['contrast'])
  for k,v in o.items():
   if isinstance(v,list):assert np.allclose(v,counterpart[k],atol=1e-11)
   elif isinstance(v,float):assert math.isclose(v,counterpart[k],abs_tol=1e-11)
   else:assert v==counterpart[k],(k,v,counterpart[k])
  comparisons.append(o)
 report['contrasts_reproduced']=True
 report['policy_phases_ms']={arm:{str(i/1e6):n for i,n in sorted(collections.Counter(policies[str(c)][arm] for c in range(nC)).items())} for arm in arms if arm not in ('random_phase','no_competitor')}
 report['matching_phase_counts']={arm:sum(policies[str(c)][arm]==policies[str(c)]['q12_full256'] for c in range(nC)) for arm in arms if arm not in ('q12_full256','random_phase','no_competitor')}
 report['zero_or_nonzero_pairs']={arm: {'joint_0':sum(groups[c,'q12_full256'][s]['wrong_output']==0 and groups[c,arm][s]['wrong_output']==0 for c in range(nC) for s in range(nS)),'q12_only_error':sum(groups[c,'q12_full256'][s]['wrong_output']==1 and groups[c,arm][s]['wrong_output']==0 for c in range(nC) for s in range(nS)),'other_only_error':sum(groups[c,'q12_full256'][s]['wrong_output']==0 and groups[c,arm][s]['wrong_output']==1 for c in range(nC) for s in range(nS)),'joint_1':sum(groups[c,'q12_full256'][s]['wrong_output']==1 and groups[c,arm][s]['wrong_output']==1 for c in range(nC) for s in range(nS))} for arm in ('global_loo','fixed_0ms','fixed_2ms','random_phase','low08','low32','low64')}
 report['bootstrap_comparisons']=comparisons;report['summaries']=summaries
 # compare identical phases deterministic
 discrepancies=[];shared=0
 for a in arms[1:]:
  if a in ('random_phase','no_competitor'):continue
  for c in range(nC):
   if policies[str(c)][a]==policies[str(c)]['q12_full256']:
    shared+=1
    for s in range(nS):
     r=groups[c,a][s];ref=groups[c,'q12_full256'][s]
     if any(r[k]!=ref[k] for k in ('wrong_output','physical_storage_ns','target_finished_ns','target_request_ns')):discrepancies.append((a,c,s))
 report['identical_phase_determinism']={'same_phase_campaign_pairs':shared,'differences_found':len(discrepancies),'examples':discrepancies[:5]}
 assert len(discrepancies)==0,discrepancies[:5]
 print('\nSUMMARIES')
 for x in summaries:print(x['policy'],x['wrong_output'],f'{100*x["wrong_fraction"]:.3f}%',f'storage {x["mean_storage_ms"]:.4f} ms')
 print('\nCONTRASTS')
 for x in comparisons:print(x)
 print('\nPHASE COUNTS')
 for k,v in report['policy_phases_ms'].items():print(k,v)
 print('matching phase counts',report['matching_phase_counts'])
 print('identical phase determinism',report['identical_phase_determinism'])
 print('Pairs',report['zero_or_nonzero_pairs'])
 Path('E1_reanalysis.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
 print('ALL CHECKS PASS. Saved reanalysis.')
