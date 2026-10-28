"""Atomic checkpoints, integrity checks and full exports (including failures)."""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile


def normalize(value):
    def scalar(obj):
        if hasattr(obj, 'item'):
            return obj.item()
        raise TypeError(type(obj).__name__)
    return json.loads(json.dumps(value, default=scalar, allow_nan=False))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    data = json.dumps(normalize(value), sort_keys=True, indent=2, allow_nan=False)+'\n'
    try:
        with temporary.open('w') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read(path):
    return json.loads(Path(path).read_text())


def write_raw(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp-'+str(os.getpid()))
    try:
        with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=3) as handle:
            json.dump(value,handle,separators=(',',':'),allow_nan=False)
            handle.write('\n')
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_raw(path):
    with gzip.open(path,'rt',encoding='utf-8') as handle:
        return json.load(handle)


def source_hashes(folder):
    folder = Path(folder)
    return {p.name:sha(p) for p in sorted(folder.iterdir()) if p.is_file() and p.suffix in ('.py','.json','.txt')}


def export_full(run_dir, output_dir):
    root = Path(run_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if root==output or root in output.parents:
        raise ValueError('Export must be outside the run folder')
    if not (root/'protocol.json').is_file():
        raise ValueError('No Q16 pilot protocol found')
    output.mkdir(parents=True,exist_ok=True)
    destination = output/('P1_Qoala_Q16_'+root.name+'_full.zip')
    temporary = destination.with_name(destination.name+'.partial-'+str(os.getpid()))
    with (root/'RUNNING.lock').open('a+') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Coordinator is still running; export after it stops') from None
        all_paths = list(root.rglob('*'))
        if any(p.is_symlink() for p in all_paths):
            raise ValueError('Unexpected symbolic link in run folder')
        paths = sorted(p for p in all_paths if p.is_file() and p.name!='RUNNING.lock')
        committed = {}
        for marker in root.glob('*/stream-*/completed.json'):
            saved = read(marker)
            for kind in ('raw','compact','statistics'):
                name = saved[kind+'_file']
                if Path(name).name!=name:
                    raise ValueError('Invalid committed filename')
                path = marker.parent/name
                if not path.is_file():
                    raise ValueError('Missing committed file: '+str(path))
                committed[path.relative_to(root).as_posix()] = saved[kind+'_sha256']
        inventory = []
        started = last = time.monotonic()
        print('Exporting all files, raw traces, source, checkpoints and failure logs...',flush=True)
        try:
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
                for index,path in enumerate(paths,1):
                    relative = path.relative_to(root).as_posix()
                    info = zipfile.ZipInfo.from_file(path,arcname=root.name+'/'+relative)
                    info.compress_type = zipfile.ZIP_STORED if path.suffix=='.gz' else zipfile.ZIP_DEFLATED
                    digest = hashlib.sha256()
                    size = 0
                    with path.open('rb') as source, archive.open(info,'w',force_zip64=True) as target:
                        for block in iter(lambda:source.read(1024*1024),b''):
                            target.write(block)
                            digest.update(block)
                            size += len(block)
                    checksum = digest.hexdigest()
                    if relative in committed and checksum!=committed[relative]:
                        raise ValueError('Committed file changed: '+relative)
                    inventory.append(dict(path=relative,bytes=size,sha256=checksum))
                    if index==len(paths) or time.monotonic()-last>15:
                        print('Archived %d/%d files (%.0f s)'%(index,len(paths),time.monotonic()-started),flush=True)
                        last = time.monotonic()
                archive.writestr('EXPORT_MANIFEST.json',json.dumps(dict(
                    archive_kind='full',complete=(root/'COMPLETE.json').exists(),
                    raw_files=sum(p.name.endswith('.raw.json.gz') for p in paths),
                    raw_files_omitted=0,committed_file_hashes_verified=len(committed),files=inventory,
                    note='Every available run file is included. Incomplete runs retain diagnostics and partial attempts; export is not a claim of pilot success.'),indent=2))
            os.replace(temporary,destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    print('Created',destination,'(%.1f MiB)'%(destination.stat().st_size/1024**2),flush=True)
    return destination
