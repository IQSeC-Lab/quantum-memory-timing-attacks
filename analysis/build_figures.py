"""Regenerate retained data, then apply the compact column-width layout."""
from pathlib import Path
import runpy
r=Path(__file__).resolve().parent
runpy.run_path(str(r/'extract_retained_data.py'),run_name='__main__')
runpy.run_path(str(r/'compact_figures.py'),run_name='__main__')
runpy.run_path(str(r/'make_diagnostic_summary.py'),run_name='__main__')
(r/'figures/diagnostics.tex').unlink(missing_ok=True)
runpy.run_path(str(r/'make_storage_mechanism.py'),run_name='__main__')
