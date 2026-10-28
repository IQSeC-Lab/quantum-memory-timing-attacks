"""Read-only environment verification. Never installs or changes a checkout."""
import importlib
import importlib.metadata
import platform
import subprocess
import sys
from pathlib import Path
import app_design as d
from app_io import read, sha

HERE = Path(__file__).resolve().parent
CORE_VERSIONS = {'netsquid':'1.1.8','pydynaa':'1.0.2','qoala':'2.1.0',
                 'numpy':'1.26.4','netqasm':'2.3.0','scipy':'1.15.3'}


def check_environment(repo):
    if not __debug__:
        raise RuntimeError('Run without -O')
    repo = Path(repo).expanduser().resolve()
    head = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    dirty = subprocess.check_output(['git','-C',str(repo),'diff','HEAD','--name-only'],text=True).strip()
    if head!=d.PIN or dirty:
        raise RuntimeError('Expected the existing clean pinned Qoala checkout')
    sys.path.insert(0,str(repo))
    versions = {k:importlib.metadata.version(k) for k in CORE_VERSIONS}
    if sys.version_info[:2]!=(3,10) or versions!=CORE_VERSIONS:
        raise RuntimeError('Use Python 3.10 with '+str(CORE_VERSIONS)+'; found '+str(versions))
    expected = read(HERE/'pinned_backend.json')
    imported = {}
    for relative,want in expected.items():
        path = repo/relative
        if sha(path)!=want:
            raise RuntimeError('Pinned backend file differs: '+relative)
        module = importlib.import_module(relative[:-3].replace('/','.'))
        actual = Path(module.__file__).resolve()
        if actual!=path.resolve() or sha(actual)!=want:
            raise RuntimeError('Imported backend differs: '+relative)
        imported[relative] = dict(path=str(actual),sha256=want)
    packages = sorted([dict(name=x.metadata['Name'],version=x.version)
        for x in importlib.metadata.distributions() if x.metadata.get('Name')],key=lambda r:r['name'].lower())
    return dict(python=sys.version,executable=sys.executable,platform=platform.platform(),
        versions=versions,repo_head=head,imported_backend=imported,installed_packages=packages,
        simulation_access='In-process Python API; independent native process per fixture; no remote service',
        timing_scope='Configured simulation times; wall time is computational cost only')


def parser_preflight():
    import json
    from qoala.lang.parse import QoalaParser
    from app_io import read
    signatures=read(HERE/'parser_signatures.json')
    def shape(text):
        p=QoalaParser(text).parse()
        return dict(blocks=[dict(name=b.name,critical_section=b.critical_section,
            op_types=[type(o).__name__ for o in b.instructions]) for b in p.blocks],
            routines={k:[str(x) for x in v.subroutine.instructions] for k,v in p.local_routines.items()})
    observed={'classical':shape(d.classical_program())}
    for variant in d.VARIANTS:
        for state in d.STATES:
            for cs in ('plain','cs'):
                spec=dict(variant=variant,policy='QUOTA_NATIVE_CS' if cs=='cs' else 'QUOTA_FCFS')
                server,client=d.rotation_programs(spec,dict(state=state))
                observed[variant+':'+state+':'+cs]=shape(server)
                observed['client']=shape(client)
    if observed!=signatures:raise ValueError('Native pinned parser differs from packaged signatures')
    return dict(status='PASS_PINNED_PARSER',signatures=len(observed))
