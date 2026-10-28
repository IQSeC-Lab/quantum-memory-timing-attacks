P1 FEEDBACK REVISION AND ADDITIVE EXPERIMENTS -- 24 OCTOBER 2026

STATUS

The revised paper incorporates completed Q16 public-clock planning evidence.
A fresh simulator-independent replay of all 192 saved native raw streams
passes and reconstructs all 49,152 measured requests exactly. This is an
audit of existing execution, not new simulation or hardware validation.

The new timing and application adapters are PENDING NATIVE EXECUTION.
Offline engineering checks pass. NetSquid, pydynaa, Qoala and NetQASM are
unavailable in the drafting environment. No synthetic or analytic data are
substituted for native results. Q12, Q13, Q14, Q16 and their protocols remain
unchanged. Historical planning data never enter future confirmation.

WHAT THIS ADDRESSES

1. Capability realism: the paper separates the supplied per-request epoch
   from the public-clock diagnostic. Q16 supplies only a periodic clock;
   actual sharing and admission permissions are still model assumptions.
   The new planner is an executable strict JSON interface built before
   private inputs; native jobs enter through normal program submission.
   This is not an OS sandbox or a demonstrated deployed provider interface.

2. Strong baselines: a new additive timing study pairs learned releases with
   all 16 fixed phases, random bursts, even spacing and an idle control.
   Every active arm uses the same four runtime-input reduction programs,
   with 30 ordinary host operations and 1.2 ms total completed CPU service.
   These programs differ from Q12's constant-input payload, so their results
   must be described as a new experiment rather than appended to Q12.

3. Replication: a separate fixed-design application study retains five
   policies, four release arms and both compilation variants at source-
   default and short-memory sensitivity scales. Six primary contrasts are
   frozen in closure_runner.py. Whole fresh streams are independent units.
   The original 32-stream comparisons remain descriptive.

PUBLIC-CLOCK PLANNING FINDING

Selected timing has 98.901% and 98.889% timely completion, below the 99%
objective in both regimes. Its loss against even timing is 0.696 and 0.586
percentage points, exceeding the allowed 0.5 point. The point estimates
therefore miss the prospective conjunction. This is not a population
failure proof, a significance test, or confirmation of service-preserving
harm. Increasing N alone does not supply the missing feasibility result.
No policy, deadline or criterion is changed after inspecting this study.

RUN ON THE EXISTING SERVER

Open P1_Feedback_Closure_Server.ipynb in the existing native environment.
It defaults to a smoke run with eight workers. It does not install software,
change credentials, modify the backend, or launch full runs automatically.

Equivalent commands, from this package directory:

  python source/offline_tests.py
  python source/closure_runner.py preflight --folder runs/environment
  python source/closure_runner.py run --family timing --mode smoke --workers 8 --folder runs/timing-smoke
  python source/closure_runner.py run --family application --mode smoke --workers 8 --folder runs/application-smoke

Supply --repo /path/to/qoala-sim if repository discovery fails. The imported
Qoala source must be clean at revision
31fa2c9699578d227736557f35cd01a134c4bbde.

After the appropriate smoke gate passes, a full run is explicit:

  python source/closure_runner.py run --family timing --mode full --workers 8 --smoke runs/timing-smoke --folder runs/timing-full
  python source/closure_runner.py run --family application --mode full --workers 8 --smoke runs/application-smoke --folder runs/application-full

Full timing: 16,384 fresh paired inputs per workload, 20 arms, 655,360
native executions. Full application: 8,192 fresh streams across 80
configurations, also 655,360 executions. These are substantial runs.
Smoke timings provide a rough compute estimate; review native storage and
cost before launching. Q16 is not scheduled for another expensive run.

INFERENCE AND INTERPRETATION

The timing study draws a label uniformly from the fixed ensemble of 32
historically learned policies on each new paired trial. Its inference is
conditional on that finite ensemble, not on a new population of trained
policies. Fresh shot identifiers begin at 1,000,000 and never reuse the
historical evaluation namespace. All active arms share private inputs.
The family contains 36 contrasts: learned minus 16 fixed rules, random
and even, for two workloads. The minimum of simultaneous fixed-rule
contrast limits bounds learned minus the best fixed rule on this grid.
This oracle diagnostic is not a deployable selector using target labels.

The application study uses new whole-stream identifiers from 1,000,000.
Its six predeclared comparisons use stream means. N=8,192 targets a
2-percentage-point half-width under a fourfold historical variance
envelope; the projected bound is 1.790 points. This is a precision plan,
not a power guarantee or an assurance that effects will be significant.
Do not extend N after looking at results or pool smoke/planning outputs.

Intervals use the fixed-N two-sided empirical Bernstein bound, union-
bounded over the declared family. If X is in [a,b], n independent IID
units have unbiased sample variance S^2, K comparisons and alpha=.05,
the radius is

  sqrt(2 S^2 log(4K/alpha)/n) + 7(b-a)log(4K/alpha)/(3(n-1)).

Here paired differences lie in [-1,1]. The calculation assumes independent
IID simulation units under each frozen design, conditional on the fixed
policy ensemble where applicable. It gives no protection against model
misspecification, biased native randomness, or hardware parameter errors.

Source: Andreas Maurer and Massimiliano Pontil, "Empirical Bernstein
Bounds and Sample Variance Penalization," COLT 2009, Theorem 4,
arXiv:0907.3740; https://arxiv.org/abs/0907.3740.
The primary paper was checked directly. Historical intervals are not
replaced by this prospectively chosen procedure.

AUDIT, RESUME AND FAILURE BEHAVIOR

Each checkpoint retains compressed native raw and independently checked
compact records plus SHA-256 hashes. Resume verifies source/protocol and
completed checkpoint hashes. Partial failures remain in failures/ and
cannot become successful checkpoints. An unfinished request is retained
in all-offered accounting. Missing infidelity or verification endpoints
block the corresponding confirmatory effect claim; they are not dropped.

The application smoke includes the unchanged parent's 96 ideal/default
functional controls, in addition to two paired application streams. The
timing smoke has noise-free controls across all arms and six input states.
Smoke output permits engineering conclusions only.

For review, return protocol.json, preflight.json, COMPLETE.json,
ANALYSIS.json, COST_ESTIMATE.json, checkpoints/, compact/, failures/ and
logs. Keep raw/ available on the server for independent audit. A compact
review archive alone cannot support a new raw-to-compact replay claim.

REPLAY EXISTING Q16

The receipt in evidence/q16_replay_receipt.json was generated from the
complete saved Q16 export, not only its compact collaborator bundle.
To independently replay it, extract the original full Q16 ZIP into an
otherwise empty directory, then run:

  python source/replay_q16.py /path/to/extracted-export --workers 4 --output q16-replay.json

Original full export identity:
P1_Qoala_Q16_q16-fresh-planning-20261014T203108751014Z_full.zip
Library file: libfile_5aa7766ca5e481919df87685c70e76e3
This package omits the 454 MB historical raw archive; it is unchanged.

The paper's claims and feedback status are documented in
FEEDBACK_RESPONSE.txt. No A* rating, venue acceptance prediction, or
completed mitigation guarantee is implied by this revision.
