import zipfile,json,gzip,hashlib,statistics,math,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path('experiments/P1_Feedback_Closure/source').resolve()))
import closure_runner as frozen
import closure_design as d
from public_policy import ARMS
z=zipfile.ZipFile('data/archives/results-all-memory-recovery-review.zip')
names=set(z.namelist())
read=lambda n:json.loads(z.read(n))
sha=lambda b:hashlib.sha256(b).hexdigest()
manifest={x['path']:x for x in read('RETAINED_RAW_MANIFEST.json')}
def bounds(x,k):
 n=len(x);v=statistics.variance(x);m=statistics.mean(x);l=math.log(4*k/.05);r=math.sqrt(2*v*l/n)+14*l/(3*(n-1))
 return dict(mean=m,variance=v,radius=r,lower=max(-1,m-r),upper=min(1,m+r),n=n)
for family in ('timing','application'):
 prefix=family+'-full/';p=read(prefix+'protocol.json');end=read(prefix+'COMPLETE.json');a=read(prefix+'ANALYSIS.json')
 assert p==frozen.protocol(family,'full')
 assert end['protocol_sha256']==sha(z.read(prefix+'protocol.json'))
 assert end['source_hashes']==p['source_hashes']==frozen.source_hashes()
 values={};agg={};benign={};native=0;raw_present=0;count=0;optimized_effect=0.
 for job in d.jobs(family,'full'):
  key=d.stable(job);receipt=read(prefix+'checkpoints/'+key+'.json');assert receipt['job']==job
  payload=z.read(prefix+receipt['compact']);assert sha(payload)==receipt['compact_sha256']
  assert manifest[prefix+receipt['raw']]['sha256']==receipt['raw_sha256']
  raw_present+=prefix+receipt['raw'] in names
  rows=json.loads(gzip.decompress(payload));assert len(rows)==receipt['native_executions'];native+=len(rows);count+=1
  if family=='timing':
   assert len(rows)==(job['end']-job['start'])*len(ARMS)
   groups={}
   for r in rows:
    assert r['workload']==job['workload'] and job['start']<=r['index']<job['end']
    g=groups.setdefault(r['index'],{});assert r['arm'] not in g;g[r['arm']]=r
    assert r['service_ns']==(0 if r['arm']=='none' else 1200000)
    key=(r['workload'],r['arm']);s=agg.setdefault(key,dict(offered=0,wrong_completed=0,unfinished=0))
    s['offered']+=1;s['unfinished']+=not r['completed'];s['wrong_completed']+=r['wrong_output'] if r['completed'] else 0
   for i,g in groups.items():
    assert set(g)==set(ARMS) and all(r['completed'] for r in g.values())
    assert len({(r['state'],r['seed'],r['target_request_ns'],r['campaign']) for r in g.values()})==1
    for arm in ARMS:
     if arm in ('none','learned'):continue
     values.setdefault((job['workload'],'learned-minus-'+arm),[]).append(g['learned']['wrong_output']-g[arm]['wrong_output'])
  else:
   assert [r['spec'] for r in rows]==list(frozen.app_specs(job['index'],'full'))
   groups={}
   for r in rows:
    s=r['spec'];key=(s['profile'],s['cost_ns'],s['variant'],s['policy'],s['arm']);assert key not in groups;groups[key]=r
    assert r['all_attacker_work_completed'] and all(o['complete'] for o in r['outcomes'])
    assert len(r['outcomes'])==12
    g=agg.setdefault(key,dict(offered=0,correct_on_time=0,wrong_on_time=0,late=0,unfinished=0,wrong_all=0,wrong_and_late=0,infidelity_sum=0.,missing_infidelity=0))
    for o in r['outcomes']:
     g['offered']+=1;g[o['category']]+=1;g['wrong_all']+=bool(o['wrong']);g['wrong_and_late']+=bool(o['wrong']) and o['category']=='late'
     if o['infidelity'] is None:g['missing_infidelity']+=1
     else:g['infidelity_sum']+=o['infidelity']
   for profile,cost,var in d.APP_CASES:
    for policy in d.POLICIES:
     active=[groups[(profile,cost,var,policy,arm)] for arm in ('random','even','selected')]
     assert len({r['executed_attacker_service_ns'] for r in active})==1
     assert len({r['private_plan_sha256'] for r in active})==1
     if var=='optimized':
      fs=[sum(o['infidelity'] for o in r['outcomes'])/12 for r in active]
      optimized_effect=max(optimized_effect,max(fs)-min(fs))
   for c in frozen.APP_CONTRASTS:
    profile,cost,var,lp,la,rp,ra,endpoint=c
    left,right=groups[(profile,cost,var,lp,la)],groups[(profile,cost,var,rp,ra)]
    assert left['private_plan_sha256']==right['private_plan_sha256']
    def value(r):
     if endpoint=='infidelity':
      assert all(o['infidelity'] is not None for o in r['outcomes'])
      return sum(o['infidelity'] for o in r['outcomes'])/12
     return sum(o['category']=='correct_on_time' for o in r['outcomes'])/12
    values.setdefault(tuple(c),[]).append(value(left)-value(right))
 for c in a['comparisons']:
  key=(c['workload'],c['contrast']) if family=='timing' else tuple(c['contrast'])
  assert len(values[key])==p['actual_N']
  v=bounds(values[key],36 if family=='timing' else 6)
  for k,x in v.items():assert abs(c[k]-x)<1e-12,(family,key,k,c[k],x)
 for old in a['all_offered_summary']:
  g=agg[tuple(old['configuration'])]
  for k,v in g.items():assert abs(old[k]-v)<1e-8,(family,old['configuration'],k)
 if family=='timing':
  for w in ('plus','teleport'):
   fixed=[bounds(values[(w,'learned-minus-'+arm)],36) for arm in ARMS if arm.startswith('fixed_')]
   for k in ('lower','upper'):assert min(c[k] for c in fixed)==a['learned_minus_best_fixed_grid'][w][k]
 assert count==end['jobs'] and native==end['native_executions']==a['native_rows']
 print(json.dumps(dict(family=family,jobs=count,native_executions=native,contrasts=len(values),protocols_source_and_compact_hashes='PASS',independent_interval_and_count_recalculation='MATCH',raw_files_available=raw_present,optimized_active_infidelity_max_range=optimized_effect)))

 if __import__('os').environ.get('QMT_EXPORT_UNITS') == '1':
  import numpy as np
  output=Path('data/analysis');output.mkdir(parents=True,exist_ok=True)
  keys=[(c['workload'],c['contrast']) if family=='timing' else tuple(c['contrast']) for c in a['comparisons']]
  np.savez_compressed(output/(family+'.npz'),units=np.asarray([values[k] for k in keys],dtype=np.float64),reference=np.asarray(json.dumps(a)))
