"""Package code and companion data while retaining every frozen package file."""
from pathlib import Path
import json
import zipfile

root = Path(__file__).resolve().parents[1]
out = root.parent / 'deliverables'
out.mkdir(exist_ok=True)
if (root / 'paper').exists():
    raise SystemExit('Remove the manuscript folder before packaging this code-only release.')
results = sorted((root / 'data/analysis').glob('*.npz'))
if len(results)!=9: raise SystemExit('The complete nine-file statistical dataset is required.')
with zipfile.ZipFile(out / 'quantum-memory-timing-attacks-code.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob('*')):
        if not p.is_file(): continue
        rel = p.relative_to(root)
        if any(x in rel.parts for x in ('.git', '__pycache__', 'outputs')): continue
        if rel.parts[:2] in [('data', 'archives'), ('data', 'analysis')] or p.suffix == '.pyc': continue
        if rel.parts[0] == 'paper': continue
        z.write(p, Path('quantum-memory-timing-attacks') / rel)
with zipfile.ZipFile(out / 'quantum-memory-timing-attacks-analysis-data.zip', 'w', zipfile.ZIP_STORED) as z:
    for p in results: z.write(p, p.relative_to(root))
print('Packaged code and companion data in', out)
