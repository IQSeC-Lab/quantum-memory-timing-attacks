"""Translate retained analyses into editable charts; does not run experiments."""
from pathlib import Path
import json,zipfile,shutil,hashlib
R=Path(__file__).resolve().parent;F=R/'figures';D=F/'data';E=R/'evidence'
archives=[('results-all-memory-recovery-review.zip',['timing','application']),('results-all-additional-memory-recovery-review.zip',['causal','robustness'])]
# Once the four analyses have been extracted, regeneration needs no large review ZIP.
for archive,families in archives:
 for family in families:
  p=E/(family+'_analysis.json')
  if not p.exists():
   with zipfile.ZipFile(R.parent/'data/archives'/archive) as z:p.write_bytes(z.read(family+'-full/ANALYSIS.json'))
t,a,c,r=[json.loads((E/(x+'_analysis.json')).read_text()) for x in ['timing','application','causal','robustness']]
q=json.loads((E/'q14_expected.json').read_text());clock=json.loads((E/'q16_descriptive.json').read_text())
manifest={}
def data(name,columns,rows):
 (D/(name+'.dat')).write_text(' '.join(columns)+'\n'+''.join(' '.join(str(x) for x in row)+'\n' for row in rows))
 manifest[name]={'columns':columns,'rows':rows}
def tex(name,s): (F/(name+'.tex')).write_text('% Editable TikZ/PGFPlots; generated from retained evidence by build_figures.py.\n'+s+'\n')
base=r'''/tikz/font=\footnotesize,tick label style={font=\scriptsize},title style={font=\footnotesize},axis lines=left,tick align=outside,grid style={black!12},scaled ticks=false'''
def forest_plot(filename,rows):
 data(filename,['y','mean','minus','plus'],[(i,100*x['mean'],100*(x['mean']-x['lower']),100*(x['upper']-x['mean'])) for i,x in enumerate(rows)])
 return r'\addplot+[only marks,mark=*,mark size=1.6pt,blue!60!black,error bars/.cd,x dir=both,x explicit] table[x=mean,y=y,x error minus=minus,x error plus=plus]{figures/data/'+filename+'.dat};\n'
# Timing: finite-family uncertainty plus the full fixed grid, not just its maximum.
rows=[next(x for x in t['comparisons'] if x['workload']==w and x['contrast']==k) for w in ['plus','teleport'] for k in ['learned-minus-random','learned-minus-even']]
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=3 by 1,horizontal sep=1.55cm},width=.275\textwidth,height=4.45cm,'+base+r''']
\nextgroupplot[title={(a) Learned minus control},xlabel={Error difference (pp)},xmin=-.4,xmax=3.7,ymin=-.5,ymax=3.5,y dir=reverse,ytick={0,1,2,3},yticklabels={Plus/random,Plus/even,Teleport/random,Teleport/even},xmajorgrids]
\draw[dashed,black!50] (axis cs:0,-.5)--(axis cs:0,3.5);
'''+forest_plot('timing_contrasts',rows)
for w,label,ylim in [('teleport','Teleportation',(7.5,12)),('plus','Stored plus state',(19,24))]:
 vals=[]
 for i in range(16):
  x=next(x for x in t['all_offered_summary'] if x['configuration']==[w,f'fixed_{i:02d}'])
  vals.append([i*.25,100*x['wrong_completed']/x['offered']])
 learned=next(x for x in t['all_offered_summary'] if x['configuration']==[w,'learned']);v=100*learned['wrong_completed']/learned['offered']
 data('grid_'+w,['phase','error'],vals)
 s+=r'\nextgroupplot[title={('+('b' if w=='teleport' else 'c')+') '+label+r'},xlabel={Fixed phase (ms)},ylabel={Error (\%)},xmin=0,xmax=3.75,ymin='+str(ylim[0])+',ymax='+str(ylim[1])+r''',xtick={0,1,2,3},ymajorgrids]
\addplot[black,mark=*,mark size=1.1pt] table[x=phase,y=error]{figures/data/grid_'''+w+r'''.dat};
\addplot[red!65!black,dashed,thick] coordinates {(0,'''+str(v)+') (3.75,'+str(v)+')};\n'
 if w=='teleport':s+=r'\node[anchor=north east,font=\scriptsize,text=red!65!black] at (rel axis cs:.98,.98) {Learned};'+'\n'
s+=r'\end{groupplot}\end{tikzpicture}';tex('timing',s)
# Frozen transfer: separate expected and sampled endpoints, both with retained intervals.
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=2 by 1,horizontal sep=1.05cm},width=.42\columnwidth,height=4.1cm,'+base+r''']
'''
for idx,(endpoint,mkey,ikey,title) in enumerate([('infidelity','effect_pp','interval_pp','Expected infidelity'),('sampled','sampled_error_pp','sampled_error_interval_pp','Verification errors')]):
 rows2=[]
 for i,x in enumerate(q['primary_contrasts']):
  m=x[mkey];lo,hi=x[ikey];rows2.append([i,m,m-lo,hi-m])
 data('transfer_'+endpoint,['y','mean','minus','plus'],rows2)
 s+=r'\nextgroupplot[title={('+('a' if idx==0 else 'b')+') '+title+r'},xlabel={Difference (pp)},ymin=-.5,ymax=3.5,y dir=reverse,ytick={0,1,2,3},yticklabels={'+('Plus A,Plus B,Teleport A,Teleport B' if idx==0 else ',,,')+r'},xmin=-.15,xmax=.9,xtick={0,.4,.8},xmajorgrids]'+'\n'
 s+=r'\draw[dashed,black!50] (axis cs:0,-.5)--(axis cs:0,3.5);'+'\n'
 s+=r'\addplot+[only marks,mark=*,mark size=1.5pt,blue!60!black,error bars/.cd,x dir=both,x explicit] table[x=mean,y=y,x error minus=minus,x error plus=plus]{figures/data/transfer_'+endpoint+'.dat};\n'
s+=r'\end{groupplot}\end{tikzpicture}';tex('transfer',s)
policies=['QUOTA_FCFS','QUOTA_RR','QUOTA_EDF_NUMERIC','QUOTA_PROTECTED','QUOTA_NATIVE_CS']
labels=['FCFS','RR','EDF','Priority','CS']
def cell(profile,variant,p,arm):return next(x for x in a['all_offered_summary'] if x['configuration']==[profile,15 if profile=='paper_default' else 10000,variant,p,arm])
# Preparation comparisons; bars are descriptive and must not gain invented marginal CIs.
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=2 by 1,horizontal sep=.9cm},width=.42\columnwidth,height=3.8cm,'+base+r''',ybar,/pgf/bar width=4pt,xmin=-.4,xmax=3.4,area legend,ymin=0,xtick={0,1,2,3},xticklabels={Idle,Random,Even,Selected},x tick label style={rotate=35,anchor=east},ylabel={Infidelity (\%)},ymajorgrids]
'''
for i,profile in enumerate(['paper_default','short_memory']):
 s+=r'\nextgroupplot[title={('+('a' if i==0 else 'b')+') '+('Source default' if i==0 else 'Short memory')+r'},ymax=20]'+'\n'
 for variant,color in [('unoptimized','black!40'),('optimized','blue!60!black')]:
  vals=[(j,100*cell(profile,variant,'QUOTA_FCFS',arm)['mean_infidelity_bounds'][0]) for j,arm in enumerate(['none','random','even','selected'])]
  data('prep_'+profile+'_'+variant,['x','value'],vals)
  s+=r'\addplot[draw=black,fill='+color+'] table[x=x,y=value]{figures/data/prep_'+profile+'_'+variant+'.dat};\n'
 if i==0:s+=r'\legend{Early preparation,Deferred preparation}'+'\n'
s=s.replace('ymajorgrids]',r'ymajorgrids,legend style={at={(.0,1.32)},anchor=south west,font=\scriptsize,draw=none},legend cell align=left]')
s=s.replace('title={(b) Short memory},ymax=20','title={(b) Short memory},ylabel={},ymax=20')
s+=r'\end{groupplot}\end{tikzpicture}';tex('preparation',s)
# Scheduler tradeoff: quality, all-offered outcomes, classical service.
data('scheduler',['x','infid','correct','wrong','late','benign'],[(i,100*(x:=cell('short_memory','unoptimized',p,'selected'))['mean_infidelity_bounds'][0],100*x['correct_on_time']/x['offered'],100*x['wrong_on_time']/x['offered'],100*x['late']/x['offered'],x['benign_p95_nearest_rank_ns']/1e6) for i,p in enumerate(policies)])
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=3 by 1,horizontal sep=1.45cm},width=.275\textwidth,height=4.2cm,'+base+r''',xtick={0,1,2,3,4},xticklabels={FCFS,RR,EDF,Priority,CS},x tick label style={rotate=30,anchor=east},xmin=-.5,xmax=4.5,ymin=0,ymajorgrids]
\nextgroupplot[title={(a) Quantum quality},ylabel={Infidelity (\%)},ymax=20,ybar,/pgf/bar width=10pt]
\addplot[fill=black!35,draw=black] table[x=x,y=infid]{figures/data/scheduler.dat};
\nextgroupplot[title={(b) All offered quantum requests},ylabel={Requests (\%)},ymax=100,ybar stacked,/pgf/bar width=12pt,legend style={font=\scriptsize,draw=none,at={(.5,1.42)},anchor=south,legend columns=3}]
\addplot[fill=blue!60!black,draw=white] table[x=x,y=correct]{figures/data/scheduler.dat};
\addplot[fill=black!35,draw=white] table[x=x,y=wrong]{figures/data/scheduler.dat};
\addplot[fill=red!65!black,draw=white] table[x=x,y=late]{figures/data/scheduler.dat};
\legend{Correct timely,Wrong timely,Late}
\nextgroupplot[title={(c) Benign classical service},ylabel={95th-percentile latency (ms)},ymax=3.4,ybar,/pgf/bar width=10pt]
\addplot[fill=black!35,draw=black] table[x=x,y=benign]{figures/data/scheduler.dat};
\end{groupplot}\end{tikzpicture}''';tex('schedulers',s)
# Diagnostics retain the full conservative intervals, including the zero-variance floor.
rowsc=[next(x for x in c['comparisons'] if x['cell']==v and x['variant']=='unoptimized' and x['policy']=='QUOTA_FCFS' and x['endpoint']=='infidelity' and x['contrast']=='selected-minus-random') for v in ['ideal','memory_only','gate_only','combined']]
rowsr=[x for x in r['comparisons'] if x['variant']=='unoptimized' and x['policy']=='QUOTA_FCFS' and x['endpoint']=='infidelity']
rlabs=['Reference','Host: 15 ns','Host: 1 us','T2: 13 ms','T2: 10 s','Link: 1 ms','Link: 5 ms','Benign: 0','Benign: 16','Budget: 0.6 ms','Budget: 2.4 ms','Phase: -1 ms','Phase: -0.5 ms','Phase: +0.5 ms','Phase: +1 ms']
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=2 by 1,horizontal sep=3.0cm},width=.36\textwidth,height=6.5cm,'+base+r''',xlabel={Selected minus random infidelity (pp)},xmajorgrids,y dir=reverse]
\nextgroupplot[title={(a) Noise controls: 512 streams},xmin=-11,xmax=11,ymin=-.5,ymax=3.5,ytick={0,1,2,3},yticklabels={Ideal,Memory only,Gate only,Combined},xtick={-10,-5,0,5,10}]
\draw[dashed,black!50] (axis cs:0,-.5)--(axis cs:0,3.5);
'''+forest_plot('causal',rowsc)
s+=r'\nextgroupplot[title={(b) Sensitivity: 256 streams},xmin=-30,xmax=30,ymin=-.5,ymax=14.5,ytick={'+','.join(str(i) for i in range(15))+'},yticklabels={'+','.join(x.replace('us',r'$\mu$s') for x in rlabs)+r'''}]
\draw[dashed,black!50] (axis cs:0,-.5)--(axis cs:0,14.5);
'''+forest_plot('robustness',rowsr)+r'\end{groupplot}\end{tikzpicture}';tex('diagnostics',s)
# Public-clock rates, descriptive; the marked lines are prespecified decision gates.
s=r'\begin{tikzpicture}\begin{groupplot}[group style={group size=2 by 1,horizontal sep=1.05cm},width=.42\columnwidth,height=4.0cm,'+base+r''',xtick={0,1},xticklabels={Renewal,Jitter},x tick label style={rotate=20,anchor=east},xmin=-.35,xmax=1.35,ymajorgrids]
\nextgroupplot[title={(a) Timely completion},ylabel={Requests (\%)},ymin=98.7,ymax=99.75,ytick={99,99.5}]
\addplot[black!50,dashed] coordinates {(-.35,99)(1.35,99)};
'''
for arm,color,mark in [('selected','red!65!black','*'),('random','black','square*'),('even','blue!60!black','triangle*')]:
 vals=[(i,next(x for x in clock['rows'] if x['arm']==arm and x['arrival']==ar)['on_time_pct']) for i,ar in enumerate(['renewal','periodic_jitter'])]
 data('clock_'+arm,['x','value'],vals)
 s+=r'\addplot[color='+color+',mark='+mark+'] table[x=x,y=value]{figures/data/clock_'+arm+'.dat};\n'
s+=r'''\nextgroupplot[title={(b) Loss against controls},ylabel={Timely loss (pp)},ymin=0,ymax=.85,ytick={0,.5}]
\addplot[black!50,dashed] coordinates {(-.35,.5)(1.35,.5)};
'''
for arm,color,mark in [('random','black','square*'),('even','blue!60!black','triangle*')]:
 vals=[(i,-next(x for x in clock['comparisons'] if x['comparator']==arm and x['arrival']==ar)['delta_on_time_pp']) for i,ar in enumerate(['renewal','periodic_jitter'])]
 data('clock_loss_'+arm,['x','value'],vals)
 s+=r'\addplot[color='+color+',mark='+mark+'] table[x=x,y=value]{figures/data/clock_loss_'+arm+'.dat};\n'
s+=r'\end{groupplot}\end{tikzpicture}';tex('clock',s)
# Primary table: bold strictly on simultaneous intervals excluding zero.
labels=[r'Selected $-$ random: infidelity',r'EDF $-$ FCFS: correct and timely',r'FCFS $-$ critical section: infidelity',r'FCFS $-$ critical section: correct and timely',r'Priority $-$ EDF: correct and timely',r'Selected $-$ random: infidelity (default)']
lines=[r'\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}lrrl@{}}\toprule',r'Paired contrast & Difference (pp) & Simultaneous 95\% interval (pp) & Conclusion \\ \midrule']
for label,x in zip(labels,a['comparisons']):
 m,lo,hi=[100*x[k] for k in ['mean','lower','upper']];sig=lo>0 or hi<0
 vals=[f'{m:+.3f}',f'[{lo:+.3f}, {hi:+.3f}]']
 if sig:vals=[r'\textbf{'+v+'}' for v in vals]
 lines.append(label+' & '+' & '.join(vals)+' & '+('Excludes zero' if sig else 'Includes zero')+r' \\')
lines.append(r'\bottomrule\end{tabular*}')
(R/'primary_contrasts.tex').write_text('\n'.join(lines)+'\n')
assert all(abs(sum(row[2:5])-100)<1e-9 for row in manifest['scheduler']['rows'])
assert len(a['comparisons'])==6 and len(t['comparisons'])==36
assert all(x['lower']<=0<=x['upper'] for x in c['comparisons']+r['comparisons'])
(E/'chart_data_audit.json').write_text(json.dumps({'status':'PASS_TRANSLATION_CHECKS','note':'Reuses independently checked retained analyses; no new inference or simulation.','tables':manifest,'input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in E.glob('*.json') if p.name!='chart_data_audit.json'}},indent=2)+'\n')
print('Generated six composite results figures (14 panels), data, and six-contrast table.')
