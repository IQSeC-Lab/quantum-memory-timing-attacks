"""Descriptive mechanism panels from matched native compact records."""
from pathlib import Path
import json,gzip,hashlib
import numpy as np
R=Path(__file__).resolve().parent;E=R/'evidence';D=R/'figures/data'
dif=json.loads(gzip.decompress((E/'mechanism_paired_storage.json.gz').read_bytes()))
rows=json.loads(gzip.decompress((E/'mechanism_xplus_records.json.gz').read_bytes()))
for variant,d in dif.items():
 x=np.sort(d);n=len(x);assert n==98304
 if np.ptp(x)==0:pts=[(-4.5,0),(x[0],0),(x[0],100),(5,100)]
 else:
  indices=np.unique(np.linspace(0,n-1,1001,dtype=int));pts=[(x[0],0)]+[(float(x[i]),100*(i+1)/n) for i in indices]
 (D/('mechanism_cdf_'+variant+'.dat')).write_text('delta_ms cdf_pct\n'+''.join(f'{x:.9f} {y:.9f}\n' for x,y in pts))
counts={}
for arm in ['random','selected']:
 a=sorted([x for x in rows if x[2]==arm],key=lambda x:(x[3],x[0],x[1]));assert len(a)==16384
 bins=np.array_split(np.array([[x[3],x[4]] for x in a]),20)
 lines=['age_ms infidelity_pct n age_min_ms age_max_ms']
 for b in bins:lines.append(f'{np.mean(b[:,0]):.12g} {np.mean(b[:,1]):.12g} {len(b)} {min(b[:,0]):.12g} {max(b[:,0]):.12g}')
 (D/('mechanism_xplus_'+arm+'.dat')).write_text('\n'.join(lines)+'\n');counts[arm]=len(a)
tex=r'''% Native recorded durations and snapshot-derived infidelity; no fitted model.
\begin{tikzpicture}[font=\scriptsize]
\begin{axis}[at={(.9cm,.85cm)},anchor=south west,scale only axis,width=3.2cm,height=2.1cm,axis lines=left,tick align=outside,/tikz/font=\scriptsize,tick label style={font=\scriptsize},label style={font=\scriptsize},xmin=-4.5,xmax=5,xtick={-4,0,4},ymin=0,ymax=100,ytick={0,50,100},xlabel={Added storage (ms)},ylabel={Requests (\%)},ymajorgrids,grid style={black!12},legend style={at={(.5,1.05)},anchor=south,draw=none,font=\scriptsize,legend columns=2,column sep=3pt}]
\draw[densely dashed,black!35] (axis cs:0,0)--(axis cs:0,100);
\addplot[black,thick,const plot] table[x=delta_ms,y=cdf_pct]{figures/data/mechanism_cdf_unoptimized.dat};
\addlegendentry{Early}
\addplot[blue!55!black,thick] table[x=delta_ms,y=cdf_pct]{figures/data/mechanism_cdf_optimized.dat};
\addlegendentry{Deferred}
\end{axis}
\node at (2.5,-.43) {(a) Selected minus random};
\begin{axis}[at={(5.1cm,.85cm)},anchor=south west,scale only axis,width=3.2cm,height=2.1cm,axis lines=left,tick align=outside,/tikz/font=\scriptsize,tick label style={font=\scriptsize},label style={font=\scriptsize},xmin=0,xmax=5.5,xtick={0,2,4},ymin=0,ymax=40,ytick={0,20,40},xlabel={Storage duration (ms)},ylabel={Infidelity (\%)},ymajorgrids,grid style={black!12},legend style={at={(.5,1.05)},anchor=south,draw=none,font=\scriptsize,legend columns=2,column sep=3pt}]
\addplot[only marks,mark=o,mark size=1.8pt,black] table[x=age_ms,y=infidelity_pct]{figures/data/mechanism_xplus_random.dat};
\addlegendentry{Random}
\addplot[only marks,mark=triangle*,mark size=1.3pt,red!65!black] table[x=age_ms,y=infidelity_pct]{figures/data/mechanism_xplus_selected.dat};
\addlegendentry{Selected}
\end{axis}
\node at (6.7,-.43) {(b) Early preparation, $X+$ input};
\end{tikzpicture}
'''
(R/'figures/storage_mechanism.tex').write_text(tex)
(E/'mechanism_plot_audit.json').write_text(json.dumps({'descriptive_only':True,'streams':8192,'paired_requests_per_variant':98304,'xplus_requests_per_arm':counts,'cdf_display':'1001 ordered ranks; maximum interior rank gap 99/98304 (about 0.101 percentage points)','bins':'20 equal-count bins per arm, sorted by age; plotted coordinates are bin means; no fitted curve or confidence interval','all_matched_private_inputs':True,'checkpoint_verification':json.loads((E/'mechanism_checkpoint_audit.json').read_text()),'all_active_service_ns':2400000,'inputs_sha256':{n:hashlib.sha256((E/n).read_bytes()).hexdigest() for n in ['mechanism_paired_storage.json.gz','mechanism_xplus_records.json.gz']}},indent=2)+'\n')
print('Storage mechanism: 98304 paired requests per variant; 16384 X+ records per arm.')
