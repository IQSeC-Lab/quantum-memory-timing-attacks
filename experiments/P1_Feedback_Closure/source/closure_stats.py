"""Fixed-N, finite-family, two-sided empirical Bernstein mean intervals.

Maurer and Pontil (2009), Theorem 4. Apply the one-sided result to both tails
and union bound over the PREDECLARED comparison family. Values must be IID
independent units (whole streams in the application study). Historical
planning data are never pooled into future evaluation. No optional stopping.
"""
import math
import statistics

def interval(values, family, alpha=.05, bounds=(-1.,1.)):
    x=list(map(float,values));n=len(x);a,b=bounds
    if n<2 or type(family) is not int or family<1 or not 0<alpha<1 or b<=a:raise ValueError('Invalid design')
    if any(not math.isfinite(v) or v<a-1e-12 or v>b+1e-12 for v in x):raise ValueError('Out of declared range')
    variance=statistics.variance(x);mean=statistics.mean(x)
    log=math.log(4*family/alpha)
    radius=math.sqrt(2*variance*log/n)+7*(b-a)*log/(3*(n-1))
    return dict(mean=mean,variance=variance,n=n,radius=radius,lower=max(a,mean-radius),upper=min(b,mean+radius),
        family=family,alpha=alpha,method='Fixed-N two-sided empirical Bernstein / Bonferroni; IID bounded units')

def planned_radius(n,variance,family,alpha=.05,span=2.):
    z=math.log(4*family/alpha)
    return math.sqrt(2*variance*z/n)+7*span*z/(3*(n-1))
