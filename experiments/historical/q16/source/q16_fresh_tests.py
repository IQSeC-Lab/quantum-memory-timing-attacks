"""Fresh study boundaries, inherited physics, provenance and failure retention."""
import copy,gzip,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import q16_fresh_design as d
import q16_fresh as server
from q16_fresh_analysis import joint,summarize
from q16_io import put,sha,source_hashes
import q16_calibration_design as cal

HERE=Path(__file__).resolve().parent

def fixture(spec):
    return dict(spec=spec,pairing_sha256=d.stable([spec['arrival'],spec['stream']]),
        requested_attacker_service_ns=960_000_000,executed_attacker_service_ns=960_000_000,
        incomplete_jobs=dict(attacker=0,benign=0),max_density_error=0.,
        audit=dict(max_attacker_period_service_ns=1_200_000),offered_all=276,offered_measured=256,
        request_rows=[dict(measured=i>=20,finished_ns=100,peer_finished_ns=100,response_ns=100,wrong_output=0) for i in range(276)])

class FreshTests(unittest.TestCase):
    def test_complete_grid_and_fresh_seed_namespaces(self):
        specs=list(d.specifications());self.assertEqual(len(specs),192)
        old={cal.seed_for(domain,stage,arrival,i) for domain in ('application-arrivals','application-states','background-arrivals','simulator')
             for stage in cal.STAGES for arrival in cal.ARRIVALS for i in range(32)}
        new={d.seed_for(domain,'planning',arrival,i) for domain in ('application-arrivals','application-states','background-arrivals','simulator')
             for arrival in d.ARRIVALS for i in range(32)}
        self.assertEqual(len(new),256);self.assertFalse(old&new)
        pairs={}
        for s in specs:
            private=d.private_inputs(s);self.assertEqual(len(private['requests']),276)
            self.assertEqual(sum(q['measured'] for q in private['requests']),256)
            self.assertTrue(all(0<=q['offered_ns']<8_000_000_000 for q in private['requests']))
            key=s['arrival'],s['stream'];pairs.setdefault(key,set()).add(d.stable(private))
        self.assertEqual(len(pairs),64);self.assertTrue(all(len(v)==1 for v in pairs.values()))

    def test_equal_work_and_fixed_policy_are_not_reselected(self):
        for arm in d.ARMS:
            plan=d.attacker_plan(arm=arm,seed=123,release_end_ns=8_000_000_000)
            self.assertEqual(len(plan),3200);self.assertEqual(sum(r['requested_service_ns'] for r in plan),960_000_000)
            for period in range(800):
                rows=plan[period*4:(period+1)*4];offsets=[r['requested_ns']-period*10_000_000 for r in rows]
                self.assertTrue(all(0<=o<=6_750_000 for o in offsets))
                if arm=='selected':self.assertEqual(offsets,[250000]*4)
                if arm=='even':self.assertEqual(offsets,[0,2250000,4500000,6750000])
        self.assertEqual(d.DEADLINE_NS,5000000);self.assertEqual(d.binding()['selected_template'],[0,250000])

    def test_public_plan_precedes_private_draws_and_is_invariant(self):
        specs=list(d.specifications());events=[];original_attack=d.attacker_plan;original_private=d.private_inputs
        def attack(**kw):events.append('public');return original_attack(**kw)
        def private(s):events.append('private');return original_private(s)
        with patch.object(d,'attacker_plan',attack),patch.object(d,'private_inputs',private):d.inputs(specs[0])
        self.assertEqual(events,['public','private'])
        for arm in d.ARMS:
            first=next(s for s in specs if s['arm']==arm and s['arrival']=='renewal' and s['stream']==0)
            second=next(s for s in specs if s['arm']==arm and s['arrival']=='periodic_jitter' and s['stream']==0)
            a,b=d.inputs(first),d.inputs(second);self.assertEqual(a['attacker'],b['attacker'])
            self.assertNotEqual(a['private'],b['private'])

    def test_inherited_engine_and_raw_auditor_are_minimal_changes(self):
        old=(HERE/'q16_calibration_engine.py').read_text()
        self.assertEqual((HERE/'q16_fresh_engine.py').read_text(),old.replace('import q16_calibration_design as d','import q16_fresh_design as d'))
        old=(HERE/'q16_calibration_audit.py').read_text().split('\ndef cohort(records, stage):')[0]
        self.assertEqual((HERE/'q16_fresh_raw_audit.py').read_text(),old.replace('import q16_calibration_design as d','import q16_fresh_design as d'))
        for name,want in d.binding()['completion_receipt']['source_sha256'].items():self.assertEqual(sha(HERE/name),want)

    def test_missing_and_late_requests_stay_in_offered_denominator(self):
        r=fixture(next(d.specifications()));r['request_rows'][20]['finished_ns']=None
        r['request_rows'][21]['response_ns']=5_000_001;r['request_rows'][21]['wrong_output']=1
        r['request_rows'][22]['wrong_output']=1;r['request_rows'][22]['response_ns']=5_000_000
        c=joint(r);self.assertEqual(c['unfinished'],1);self.assertEqual(c['completed_late'],1)
        self.assertEqual(c['wrong_on_time'],1);self.assertEqual(c['Y'],1/256);self.assertEqual(c['O'],254/256)

    def test_summary_rejects_duplicate_grid_and_keeps_service_limits(self):
        rows=[fixture(s) for s in d.specifications()];s=summarize(rows)
        self.assertFalse(s['confirmatory_evaluation']);self.assertEqual(s['measured_requests'],49152)
        with self.assertRaises(ValueError):summarize(rows[:-1])
        with self.assertRaises(ValueError):summarize(rows+[copy.deepcopy(rows[0])])
        rows[0]['request_rows'][20]['finished_ns']=None;rows[0]['executed_attacker_service_ns']-=300000
        s=summarize(rows);self.assertEqual(s['status'],'PLANNING_COMPLETE_WITH_SERVICE_LIMITS');self.assertFalse(s['equal_executed_work'])

    def test_checkpoint_integrity_and_failure_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'q16-fresh-planning-test';folder=root/'planning/stream-0000';folder.mkdir(parents=True)
            spec=next(d.specifications());job=dict(spec=spec,source_sha256=source_hashes(HERE));put(folder/'job.json',job)
            raw=folder/'test.raw.json.gz';raw.write_bytes(gzip.compress(b'{"synthetic":true}'))
            put(folder/'test.compact.json',fixture(spec));put(folder/'test.stats.json',dict(wall_seconds=1))
            marker=dict(job_sha256=d.stable(job))
            for kind,name in [('raw',raw.name),('compact','test.compact.json'),('statistics','test.stats.json')]:
                marker[kind+'_file']=name;marker[kind+'_sha256']=sha(folder/name)
            put(folder/'completed.json',marker);server.verified_checkpoint(folder,job)
            original=raw.read_bytes();raw.write_bytes(original+b'changed')
            with self.assertRaises(RuntimeError):server.verified_checkpoint(folder,job)
            raw.write_bytes(original);put(root/'protocol.json',d.protocol(source_hashes(HERE)))
            put(root/'STOPPED-fixture.json',dict(error='synthetic failure'))
            output=Path(temp)/'exports';server.export(SimpleNamespace(run_dir=root,output_dir=output))
            import zipfile
            full=next(output.glob('*_full.zip'));review=next(output.glob('*_review.zip'))
            with zipfile.ZipFile(full) as z:
                self.assertEqual(z.read(root.name+'/planning/stream-0000/test.raw.json.gz'),original)
                self.assertIn(root.name+'/STOPPED-fixture.json',z.namelist())
            with zipfile.ZipFile(review) as z:
                self.assertNotIn(root.name+'/planning/stream-0000/test.raw.json.gz',z.namelist())
                self.assertEqual(len(json.loads(z.read('REVIEW_MANIFEST.json'))['omitted_files']),1)

if __name__=='__main__':unittest.main(verbosity=2)
