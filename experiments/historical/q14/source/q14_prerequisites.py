"""Verify reviewed parent identities and completed raw-audit reports, read-only."""
from pathlib import Path
import argparse
import hashlib
import json
import q14_design as d
from q13_raw_checks import need,read_json,sha_file,member

HERE=Path(__file__).resolve().parent


def defaults(project,study,run_dir):
    expected=read_json(HERE/'q14_expected_prerequisites.json')[study]
    return Path(project)/'audits'/(Path(run_dir).name+'-raw-'+expected['audit_bundle_sha256'][:12])


def verify_parent(study,run_dir,audit_dir,expected):
    run_dir=Path(run_dir).expanduser().resolve();audit_dir=Path(audit_dir).expanduser().resolve()
    need(run_dir.is_dir(),'Missing '+study.upper()+' run directory: '+str(run_dir))
    for name,digest in expected['header_sha256'].items():
        p=run_dir/name
        need(p.is_file() and not p.is_symlink() and sha_file(p)==digest,'Reviewed '+study.upper()+' metadata differs: '+str(p))
    for name,digest in expected['source_hashes'].items():
        need(sha_file(member(run_dir/'source',name))==digest,'Parent source differs: '+study+'/'+name)
    marker_path=audit_dir/'AUDIT_COMPLETE.json'
    need(marker_path.is_file(),'Run the '+study.upper()+' raw-audit notebook first. Expected PASS marker: '+str(marker_path))
    need(not (audit_dir/'AUDIT_FAILED.json').exists(),'The '+study.upper()+' audit reports failure; export its diagnostics first')
    marker=read_json(marker_path);report=read_json(audit_dir/'audit_report.json');identity=read_json(audit_dir/'audit_identity.json')
    need(marker.get('report_sha256')==sha_file(audit_dir/'audit_report.json'),'Parent audit report hash mismatch')
    need(marker.get('status')==report.get('status')=='PASS','Parent raw audit is not PASS')
    need(report['version']==identity['version']==expected['audit_version'],'Unexpected parent auditor version')
    need(report['audit_code_sha256']==identity['audit_code_sha256']==expected['audit_code_sha256'],'Parent audit code identity mismatch')
    need(identity['audit_code_hashes']==expected['audit_code_hashes'],'Parent audit source hashes differ')
    for name,digest in expected['audit_code_hashes'].items():
        need(sha_file(member(audit_dir/'audit_source',name))==digest,'Saved auditor source mismatch: '+name)
    need(identity['expected_header_sha256']==expected['header_sha256'] and identity['expected_source_hashes']==expected['source_hashes'],'Parent audit concerns different original files')
    need(report['reviewed_archive_sha256']==expected['review_archive_sha256'],'Parent review identity differs')
    need(Path(report['run_dir']).resolve()==run_dir==Path(identity['run_dir']).resolve(),'Parent audit belongs to a different run path')
    total=sum(expected['counts'].values())
    need(marker['audited_trials']==report['audited_trials']==total and report['committed_batches']==expected['committed_batches'],'Parent audit incomplete')
    need({s:m['trials'] for s,m in report['stage_metrics'].items()}==expected['counts'],'Parent audited stage counts differ')
    for name,key in [('audit_receipts.jsonl.gz','audit_receipts_sha256'),('fixed_raw_samples.jsonl.gz','fixed_raw_samples_sha256')]:
        need(sha_file(member(audit_dir,name))==report[key],'Parent audit evidence checksum mismatch')
    stable_identity=dict(review_archive_sha256=expected['review_archive_sha256'],header_sha256=expected['header_sha256'],
        original_source_hashes=expected['source_hashes'],audit_code_sha256=expected['audit_code_sha256'],
        audit_version=expected['audit_version'],audited_trials=total,committed_batches=expected['committed_batches'])
    evidence=dict(run_dir=str(run_dir),audit_dir=str(audit_dir),audit_report_sha256=marker['report_sha256'],
        audit_identity_sha256=sha_file(audit_dir/'audit_identity.json'),report=report,identity=identity,completion=marker)
    return stable_identity,evidence


def verify(q12_run,q13_run,q12_audit,q13_audit):
    expected=read_json(HERE/'q14_expected_prerequisites.json')
    identities={};evidence={}
    for study,run,audit in [('q12',q12_run,q12_audit),('q13',q13_run,q13_audit)]:
        identities[study],evidence[study]=verify_parent(study,run,audit,expected[study])
    full=read_json(Path(q12_run)/'frozen_selections.json')
    policies={key:dict(phase_ns=value['phase_ns'],source_observations_sha256=value['observations_sha256']) for key,value in full.items()}
    d.validate_policies(policies)
    # Read the already audited calibration totals, never Q12 evaluation contrasts.
    costs=read_json(Path(q12_run)/'analysis.json')['calibration_costs']
    need(set(costs)==set(policies),'Missing inherited calibration costs')
    for key,values in costs.items():
        need(values['own_application_executions']==4608 and values['competing_cpu_service_ns']==5222400000,'Unexpected source calibration budget')
    identities.update(frozen_policies_sha256=d.stable(policies),inherited_calibration_costs_sha256=d.stable(costs),
                      hardware_models_sha256=sha_file(HERE/'hardware_models.json'))
    # Validate that the transferred memory model is precisely the audited Q13 source.
    need(identities['hardware_models_sha256']==expected['q13']['source_hashes']['hardware_models.json'],'Memory model changed')
    return dict(identity=identities,evidence=evidence,policies=policies,inherited_costs=costs)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('q12-run','q13-run','q12-audit','q13-audit'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=verify(a.q12_run,a.q13_run,a.q12_audit,a.q13_audit)
    print('Q12 AND Q13 RAW-AUDIT PREREQUISITES PASSED.',flush=True)
    print('Unchanged learned policies:',len(result['policies']),'| policy hash:',result['identity']['frozen_policies_sha256'],flush=True)
    print('No new calibration. Every source campaign is retained.',flush=True)


if __name__=='__main__':main()
