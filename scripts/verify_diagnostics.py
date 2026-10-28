import zipfile,json,gzip,hashlib,math,statistics,sys
from pathlib import Path
sys.path.insert(0,str(Path('experiments/P1_Additional_Studies/source').resolve()))
import study_design as d
z=zipfile.ZipFile('data/archives/results-all-additional-memory-recovery-review.zip')
names=set(z.namelist())
read=lambda n:json.loads(z.read(n))
sha=lambda b:hashlib.sha256(b).hexdigest()
manifest={x['path']:x for x in read('RETAINED_RAW_MANIFEST.json')}
for family in ('causal','robustness'):
 base=family+'-full/';p=read(base+'protocol.json');reported=read(base+'ANALYSIS.json')
 frozen=d.protocol(family,'full');frozen['source_hashes']=json.loads(Path('experiments/P1_Additional_Studies/SOURCE_HASHES.json').read_text())
 assert p==frozen
 assert read(base+'COMPLETE.json')['protocol_sha256']==sha(z.read(base+'protocol.json'))
 values=[[] for _ in p['contrasts']];agg={};executions=0;raw_present=0
 def endpoint(row,key):
  o=row['outcomes'];assert len(o)==12
  if key=='infidelity':
   assert all(x['infidelity'] is not None for x in o)
   return sum(x['infidelity'] for x in o)/12
  return sum(x['category'] in (('correct_on_time',) if key=='correct_timely' else ('correct_on_time','wrong_on_time')) for x in o)/12
 for i in range(p['N']):
  rec=read(base+'checkpoints/%06d.json'%i);assert rec['index']==i
  payload=z.read(base+rec['compact']);assert sha(payload)==rec['compact_sha256']
  assert manifest[base+rec['raw']]['sha256']==rec['raw_sha256']
  raw_present+=base+rec['raw'] in names
  rows=json.loads(gzip.decompress(payload));assert [r['spec'] for r in rows]==list(d.specs(family,i))
  assert rec['native_executions']==len(rows);executions+=len(rows)
  lookup={}
  for row in rows:
   s=row['spec'];key=(s['diagnostic_cell']['name'],s['variant'],s['policy'],s['arm']);assert key not in lookup;lookup[key]=row
   assert row['all_attacker_work_completed']
   g=agg.setdefault(key,dict(offered=0,correct_on_time=0,wrong_on_time=0,late=0,unfinished=0,infidelity_sum=0.,missing_infidelity=0,wrong_all=0,wrong_and_late=0,attacker_offered=0,attacker_incomplete=0,attacker_service_ns=0,benign_offered=0,benign_incomplete=0,benign_timely=0,max_live_owners=0,benign_times=[]))
   for o in row['outcomes']:
    g['offered']+=1;g[o['category']]+=1;g['wrong_all']+=bool(o['wrong']);g['wrong_and_late']+=bool(o['wrong']) and o['category']=='late'
    if o['infidelity'] is None:g['missing_infidelity']+=1
    else:g['infidelity_sum']+=o['infidelity']
   g['attacker_offered']+=len(row['attacker']);g['attacker_incomplete']+=sum(not x['complete'] for x in row['attacker']);g['attacker_service_ns']+=row['executed_attacker_service_ns']
   g['benign_offered']+=len(row['benign']);g['benign_incomplete']+=sum(not x['complete'] for x in row['benign']);g['benign_timely']+=sum(x['complete'] and x['response_ns']<=5e6 for x in row['benign']);g['benign_times'].extend(x['response_ns'] for x in row['benign'] if x['complete']);g['max_live_owners']=max(g['max_live_owners'],row['max_live_owners'])
  def delta(cell,v,policy,key):
   a,b=[lookup[(cell,v,policy,arm)] for arm in ('selected','random')]
   assert a['private_plan_sha256']==b['private_plan_sha256']
   assert a['executed_attacker_service_ns']==b['executed_attacker_service_ns']
   return endpoint(a,key)-endpoint(b,key)
  for j,c in enumerate(p['contrasts']):
   v=delta(c['cell'],c['variant'],c['policy'],c['endpoint'])
   if 'reference_cell' in c:v-=delta(c['reference_cell'],c['variant'],c['policy'],c['endpoint'])
   values[j].append(v)
 for c,x,old in zip(p['contrasts'],values,reported['comparisons']):
  assert old['n']==len(x) and old['missing_paired_streams']==old['unmatched_completed_service_streams']==0
  mean=statistics.mean(x);var=statistics.variance(x);span=4 if 'reference_cell' in c else 2
  log=math.log(4*len(values)/.05);rad=math.sqrt(2*var*log/len(x))+7*span*log/(3*(len(x)-1))
  limit=span/2
  for k,v in dict(mean=mean,variance=var,radius=rad,lower=max(-limit,mean-rad),upper=min(limit,mean+rad)).items():assert abs(v-old[k])<1e-12,(family,c,k,v,old[k])
 for old in reported['all_offered']:
  g=agg[tuple(old['cell'])];times=sorted(g.pop('benign_times'));g['benign_p95_completed_only_ns']=times[max(0,(95*len(times)+99)//100-1)] if times else None
  g['infidelity_all_offered_bounds']=[g['infidelity_sum']/g['offered'],(g['infidelity_sum']+g['missing_infidelity'])/g['offered']]
  assert dict(old,cell=None)==dict(g,cell=None)
 print(json.dumps(dict(family=family,protocol_and_compact_hashes='PASS',independent_compact_recalculation='EXACT_MATCH',streams=p['N'],native_executions=executions,contrasts=len(values),raw_files_in_archive=raw_present,full_raw_replay='SEPARATE_COMPLETED_RECEIPTS_IN_DATA_ARCHIVES')))
 optimized=[c for c in reported['comparisons'] if c['variant']=='optimized' and c['endpoint']=='infidelity']
 print('optimized max abs infidelity contrast',max(abs(c['mean']) for c in optimized))
 print('unfinished',sum(g['unfinished'] for g in agg.values()),'missing infidelity',sum(g['missing_infidelity'] for g in agg.values()))

 if __import__('os').environ.get('QMT_EXPORT_UNITS') == '1':
  import numpy as np
  output=Path('data/analysis');output.mkdir(parents=True,exist_ok=True)
  np.savez_compressed(output/(family+'.npz'),units=np.asarray(values,dtype=np.float64),reference=np.asarray(json.dumps(reported)))
