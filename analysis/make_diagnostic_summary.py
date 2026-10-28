"""Summarize the retained diagnostic families without new inference."""
from pathlib import Path
import json
R=Path(__file__).resolve().parent
lines=[r'\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lrrr@{}}\toprule',r'Study & Streams & Contrasts & Excluding zero \\ \midrule']
report=[]
for family,label,expected_n,expected_k in [('causal','Noise controls',512,140),('robustness','Sensitivity',256,450)]:
 a=json.loads((R/'evidence'/(family+'_analysis.json')).read_text());cs=a['comparisons']
 assert len(cs)==expected_k and all(x['n']==expected_n for x in cs)
 count=sum(x['lower']>0 or x['upper']<0 for x in cs)
 lines.append(f'{label} & {expected_n:,} & {expected_k:,} & {count}'+r' \\')
 report.append(dict(study=label,streams=expected_n,contrasts=expected_k,intervals_excluding_zero=count))
lines.append(r'\bottomrule\end{tabular*}')
(R/'diagnostic_summary.tex').write_text('\n'.join(lines)+'\n')
(R/'evidence/diagnostic_summary_audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(report)
