"""Recompose retained plot data at IEEE column width; no new inference."""
from pathlib import Path
import json
R=Path(__file__).resolve().parent;F=R/'figures';D=F/'data';E=R/'evidence'
base=r'scale only axis,axis lines=left,tick align=outside,/tikz/font=\scriptsize,tick label style={font=\scriptsize},label style={font=\scriptsize},grid style={black!12},scaled ticks=false'
def axis(x,y,w,h,opts,body):return r'\begin{axis}[at={('+str(x)+'cm,'+str(y)+'cm)},anchor=south west,width='+str(w)+'cm,height='+str(h)+'cm,'+base+','+opts+']\n'+body+'\n'+r'\end{axis}'+'\n'
def node(x,y,text,opts=''):return r'\node['+opts+'] at ('+str(x)+','+str(y)+') {'+text+'};\n'
def save(name,s): (F/(name+'.tex')).write_text('% Column-width, editable TikZ. Panel captions are below plots.\n'+r'\begin{tikzpicture}[font=\scriptsize]'+'\n'+s+r'\end{tikzpicture}'+'\n')
def ci(filename):return r'\addplot[only marks,mark=*,mark size=1.3pt,color=blue!55!black,error bars/.cd,x dir=both,x explicit] table[x=mean,y=y,x error minus=minus,x error plus=plus]{figures/data/'+filename+'.dat};'
def zero(lo,hi):return r'\draw[dashed,black!45] (axis cs:0,'+str(lo)+')--(axis cs:0,'+str(hi)+');\n'
# Timing: a full-width forest above two fixed-grid panels.
s=axis(2.0,3.75,6.25,1.35,r'xmin=-.4,xmax=3.7,ymin=-.5,ymax=3.5,y dir=reverse,ytick={0,1,2,3},yticklabels={Plus/random,Plus/even,Teleport/random,Teleport/even},xtick={0,1,2,3},xlabel={Error difference (pp)},xmajorgrids',zero(-.5,3.5)+ci('timing_contrasts'))
s+=node(4.5,2.65,'(a) Learned minus equal-work controls')
for i,(w,lab,limits) in enumerate([('teleport','Teleportation',(7.5,12)),('plus','Stored plus state',(19,24))]):
 a=json.loads((E/'timing_analysis.json').read_text());v=next(z for z in a['all_offered_summary'] if z['configuration']==[w,'learned']);rate=100*v['wrong_completed']/v['offered']
 body=r'\addplot[black,mark=*,mark size=.8pt] table[x=phase,y=error]{figures/data/grid_'+w+'.dat};\n'+r'\addplot[red!65!black,dashed] coordinates {(0,'+str(rate)+') (3.75,'+str(rate)+')};'
 s+=axis(.8+4.15*i,.8,3.25,1.5,'xmin=0,xmax=3.75,ymin='+str(limits[0])+',ymax='+str(limits[1])+r',xtick={0,1,2,3},xlabel={Phase (ms)},ylabel={Error (\%)},ymajorgrids',body)
 s+=node(2.4+4.15*i,-.43,'('+('b' if i==0 else 'c')+') '+lab)
save('timing',s)
# Transfer: shared row labels and a narrow gap.
s=''
for i,(endpoint,label) in enumerate([('infidelity','Expected infidelity'),('sampled','Verification errors')]):
 opts=r'xmin=-.15,xmax=.9,ymin=-.5,ymax=3.5,y dir=reverse,xtick={0,.4,.8},ytick={0,1,2,3},xlabel={Difference (pp)},xmajorgrids,yticklabels={'+('Plus A,Plus B,Teleport A,Teleport B' if i==0 else ',,,')+'}'
 s+=axis(1.65+3.45*i,.85,2.95,1.45,opts,zero(-.5,3.5)+ci('transfer_'+endpoint))
 s+=node(3.1+3.45*i,-.43,'('+('a' if i==0 else 'b')+') '+label)
save('transfer',s)
# Preparation: common y scale and one compact legend.
s=r'\filldraw[fill=black!35,draw=black] (1.4,2.9) rectangle (1.7,3.04);'+node(1.8,2.97,'Early', 'anchor=west')
s+=r'\filldraw[fill=blue!55!black,draw=black] (3.8,2.9) rectangle (4.1,3.04);'+node(4.2,2.97,'Deferred','anchor=west')
for i,(profile,label) in enumerate([('paper_default','Published setting'),('short_memory','Short memory')]):
 body=''
 for variant,color in [('unoptimized','black!35'),('optimized','blue!55!black')]:body+=r'\addplot[draw=black,fill='+color+'] table[x=x,y=value]{figures/data/prep_'+profile+'_'+variant+'.dat};\n'
 opts=r'ybar,/pgf/bar width=4pt,xmin=-.4,xmax=3.4,ymin=0,ymax=20,xtick={0,1,2,3},xticklabels={Idle,Random,Even,Selected},x tick label style={rotate=30,anchor=east},ytick={0,10,20},ymajorgrids,'+(r'ylabel={Infidelity (\%)}' if i==0 else 'yticklabels={,,}')
 s+=axis(.8+4.1*i,.85,3.35,1.65,opts,body)
 s+=node(2.4+4.1*i,-.12,'('+('a' if i==0 else 'b')+') '+label)
save('preparation',s)
# Scheduling: align five policy rows across quality, outcome composition, and p95.
rows=[]
for ln in (D/'scheduler.dat').read_text().splitlines()[1:]:rows.append(list(map(float,ln.split())))
s=''
for pos,col,label in [(1.1,'blue!55!black','Correct/timely'),(4.15,'black!35','Wrong/timely'),(6.95,'red!65!black','Late')]:
 s+=rf'\fill[{col}] ({pos},3.33) rectangle ({pos+.23},3.48);'+node(pos+.3,3.4,label,'anchor=west')
for row,label in zip(rows,['FCFS','RR','EDF','Priority','CS']):
 i,infid,correct,wrong,late,benign=row;y=2.9-.44*i
 s+=node(.9,y,label,'anchor=east')
 s+=rf'\filldraw[fill=black!35,draw=black,line width=.25pt] (1.15,{y-.12}) rectangle ({1.15+infid*1.55/20},{y+.12});'+'\n'
 left=3.25
 for val,color in [(correct,'blue!55!black'),(wrong,'black!35'),(late,'red!65!black')]:
  right=left+val*3.75/100
  s+=rf'\filldraw[fill={color},draw=white,line width=.2pt] ({left},{y-.13}) rectangle ({right},{y+.13});'+'\n'
  if color=='blue!55!black':s+=node((left+right)/2,y,f'{val:.1f}', 'text=white')
  left=right
 s+=node(7.95,y,f'{benign:.3f}')
for start,width,maximum,ticks in [(1.15,1.55,20,[0,10,20]),(3.25,3.75,100,[0,50,100])]:
 s+=rf'\draw[thin] ({start},.75)--({start+width},.75);'
 for t in ticks:
  x=start+width*t/maximum;s+=rf'\draw ({x},.75)--({x},.67);'+node(x,.46,str(t))
s+=node(1.95,.07,r'(a) Infidelity (\%)')+node(5.15,.07,r'(b) Requests (\%)')+node(7.95,.07,'(c) p95 (ms)')
save('schedulers',s)
# Public clock: two small panels, common legend.
s=''
for j,(arm,color,mark,label) in enumerate([('selected','red!65!black','*','Selected'),('random','black','square*','Random'),('even','blue!55!black','triangle*','Even')]):
 x=1.2+j*2.3;s+=rf'\draw[{color}] ({x},2.8)--({x+.35},2.8);'+node(x+.43,2.8,label,'anchor=west')
for i,label in enumerate(['Timely completion','Loss against controls']):
 body=r'\addplot[black!45,dashed] coordinates {(-.3,'+('99' if i==0 else '.5')+') (1.3,'+('99' if i==0 else '.5')+')};\n'
 for arm,color,mark in [('selected','red!65!black','*'),('random','black','square*'),('even','blue!55!black','triangle*')]:
  if i==1 and arm=='selected':continue
  body+=r'\addplot[color='+color+',mark='+mark+',mark size=1.4pt] table[x=x,y=value]{figures/data/clock_'+('loss_' if i else '')+arm+'.dat};\n'
 opts=r'xmin=-.3,xmax=1.3,xtick={0,1},xticklabels={Renewal,Jitter},ymajorgrids,'+(r'ymin=98.7,ymax=99.75,ytick={99,99.5},ylabel={Timely (\%)}' if i==0 else r'ymin=0,ymax=.85,ytick={0,.5},ylabel={Loss (pp)}')
 s+=axis(.9+4.1*i,.7,3.2,1.6,opts,body)+node(2.5+4.1*i,-.02,'('+('a' if i==0 else 'b')+') '+label)
save('clock',s)
# Schematic, with both panel labels below.
s=r'''\node[draw,align=center,inner sep=2pt] (own) at (.65,2.0) {Own\\requests};
\node[draw,align=center,inner sep=2pt] (frozen) at (2,2.0) {Freeze\\phase};
\node[draw,align=center,inner sep=2pt] (target) at (3.4,2.0) {Fresh\\targets};
\draw[-{Latex}] (own)--(frozen);\draw[-{Latex}] (frozen)--(target);
\draw[-{Latex}] (.2,.95)--(4.05,.95);
\fill[blue!55!black] (.65,.90) rectangle (3.55,1.0);
\draw[red!65!black,line width=2.5pt] (2.0,1.2)--(3.0,1.2);
\node[above] at (2.5,1.23) {Competing CPU};
\node[below] at (.65,.88) {Prepare};\node[below] at (3.55,.88) {Verify};
\node[anchor=west] at (4.45,2.35) {Early};
\draw[-{Latex}] (4.5,1.95)--(8.5,1.95);
\fill[blue!55!black] (4.7,1.90) rectangle (8.2,2.0);
\node[above] at (5.65,2.03) {Reply 1};\node[above] at (7.0,2.03) {Reply 2};
\node[anchor=west] at (4.45,1.30) {Deferred};
\draw[-{Latex}] (4.5,.95)--(8.5,.95);
\fill[blue!55!black] (7.65,.90) rectangle (8.2,1.0);
\node[below] at (5.65,.85) {Reply 1};\node[below] at (7.0,.85) {Reply 2};
\node at (2.15,.05) {(a) Calibrate and evaluate};
\node at (6.5,.05) {(b) Preparation placement};'''
save('protocol',s)
# Three-column table; significance comes from unrounded retained intervals.
a=json.loads((E/'application_analysis.json').read_text())
labels=[r'Selected $-$ random: $I$',r'EDF $-$ FCFS: $S$',r'FCFS $-$ CS: $I$',r'FCFS $-$ CS: $S$',r'Priority $-$ EDF: $S$',r'Selected $-$ random: $I$ (published)']
lines=[r'\begin{tabular*}{\columnwidth}{@{\extracolsep{\fill}}lrr@{}}\toprule',r'Contrast & Estimate & 95\% interval \\ \midrule']
for lab,x in zip(labels,a['comparisons']):
 vals=[f"{100*x['mean']:+.3f}",f"[{100*x['lower']:+.3f}, {100*x['upper']:+.3f}]"]
 if x['lower']>0 or x['upper']<0:vals=[r'\textbf{'+v+'}' for v in vals]
 lines.append(lab+' & '+' & '.join(vals)+r' \\')
lines.append(r'\bottomrule\end{tabular*}')
(R/'primary_contrasts.tex').write_text('\n'.join(lines)+'\n')
print('Recomposed 6 figures at column width; moved all panel captions below plots.')
