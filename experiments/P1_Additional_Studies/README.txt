P1 RIGOROUS COMPARISON AND ADDITIONAL EXPERIMENTS
24 October 2026. Attack paper for ICC 2027; separate from the admission proposal.

WHAT A RIGOROUS COMPARISON MUST ESTABLISH

The central question is whether release timing, at the same completed competing
service, changes quantum quality and useful service after applicable program
optimization. Improving one endpoint by making more requests late is a tradeoff,
not an unconditional defense. More trials cannot repair a mismatched baseline,
removable storage wait, privileged timing information, or a missed service gate.

1. Identify each baseline precisely.

   Already evaluated: FCFS, tenant RR, numerical application EDF, ready-only
   live-state priority, native live-region critical sections, and a handwritten
   reconstruction of the published n=2 rotation optimization. The existing
   pending notebook enlarges those comparisons and adds all 16 fixed attack
   phases, random bursts and even spacing to the timing experiment.

   Oslovich et al., QCE 2025, arXiv:2505.06162, DOI
   10.1109/QCE65121.2025.00084: the rotation reconstruction is a legitimate
   mechanism/example comparison. It is not a run of the entire compiler, their
   BQC evaluation, or their selfish/M-cooperative block-deadline strategy.
   Our numerical application EDF must not be relabeled as that strategy.

   Bernheim et al., QSW 2026, DOI 10.1109/QSW72780.2026.00012: an actual
   qoala-mlir comparison remains unfinished. This package contains a pinned
   upstream translation/parser compatibility gate, not an end-to-end benchmark.
   Three exact MIT-licensed upstream LIR fixtures and their hashes are included.
   No rewriting of generated iQoala is permitted to manufacture compatibility.
   Passing this gate establishes translation/parser compatibility only; it does
   not establish native semantics, optimize the application, or compare effects.
   The current upstream commit is a reproducible software snapshot; it is not
   asserted to be the exact artifact used for the published paper.

   Marchese et al., QCNC 2026, DOI 10.1109/QCNC69040.2026.00046: obtain the
   complete Haar TUF method and any dispatch rule before implementation. The
   verified abstract is insufficient. The Delft full-text copy is embargoed
   until 7 November 2026; another authorized copy may exist. No TUF implementation
   or TUF scheduler is supplied here. Do not call our live-state priority policy
   the published TUF method. Contacting authors requires the user's instruction.

   A paper need not execute every cited system. It must execute the strongest
   applicable alternatives for its actual claim, explain exclusions, and avoid
   claiming superiority to methods it did not run.

2. Match the experiment, not just the simulator name.

   Use the same offered quantum/benign requests, private inputs, network model,
   memory/gate model, deadlines, quotas, block limits, timing information and
   stopping horizon. Within each active attack contrast, match completed
   competing service and program outputs. Optimization may legitimately reduce
   gate count: preserve application semantics, rather than padding gates away.
   Compare both no-interference performance and interference degradation; a
   higher-quality baseline can have a different starting point.

   Compile each application with the real upstream tool in an isolated toolchain
   environment. Pin compiler, frontend and runtime revisions; retain commands,
   binaries/build provenance, cost flags, source/intermediate/generated code,
   compilation time, solve time, failures and timeout counts. Upstream compiler
   durations are in nanoseconds at the inspected snapshot. Defaults differ from
   our simulator costs and must be supplied explicitly. Validate the supported
   noiseless inputs and branches before scheduling traffic. Reproduce upstream
   tests with FileCheck/full test suite where possible. A parser pass alone is
   never the required end-to-end comparison.

   Keep the frozen P1 runtime unchanged. If a compiler requires another runtime,
   run ALL comparison policies there in a separate versioned study and include
   an overlap case against the frozen runtime. Record bridges and new assumptions.

3. Use the correct statistical and service units.

   Analyze a whole independent stream, not correlated requests as independent
   samples. Matching a seed does not imply identical native measurement RNG
   consumption across schedules. Hold calibration separate from evaluation; freeze policies, sample
   sizes, endpoints and comparison families before evaluation. Include fixed
   timing and cheap no-learning alternatives. Count every offered request in
   correct/on-time, wrong/on-time, late and unfinished categories. Report wrong
   AND late overlap separately when making error claims. Report missing-quality
   bounds, all competing service, benign completion/latency and fairness.

   Native internal checks, independent replay of saved raw traces, and physical
   hardware validation are distinct. Raw replay does not independently simulate
   noisy physics. NetSquid execution is not evidence of deployed permissions,
   hardware timing, or an authenticated operating-system attack interface.

PREPARED ADDITIONAL STUDIES

Both reuse the unchanged native application executor and independent trace
auditor. Only the versioned new settings and traffic generator are installed in
isolated worker processes. They preserve the six-state, two-angle n=2 rotation
semantics. Every active job remains the identical 28-input, 30-operation
reduction. Historical Q12/Q13/Q14/Q16 and the pending experiment package are
neither modified nor rerun. New stream IDs start at 2,000,000.

E1. Causal noise controls (diagnostic, fixed N=512 streams).
    Hypothesis: timing-dependent quality degradation is attributable to memory
    noise exposure, rather than merely instruction count or gate noise.
    Four channel interventions: all ideal, memory-only, gate-only, combined.
    Gate durations and instruction costs stay fixed across interventions.
    Short-memory slice: native Markovian T2=4ms, host cost=10us, link=155us,
    26.6us gates; noisy gate fidelity=.99. Both compilation variants; all five
    scheduling policies; idle, random burst, frozen selected phase.
    Per stream: 4 x 2 x 5 x 3 = 120 native executions.
    Full: 61,440 executions. Smoke: 240 executions (2 streams).
    140 predeclared paired contrasts: 120 selected-minus-random contrasts
    (infidelity, correct-timely, timely), plus 20 infidelity interactions
    (memory-only minus ideal; combined minus gate-only). Interaction ranges
    are [-2,2], rather than the [-1,1] of a single paired rate difference.
    A negative/null finding must be retained, including optimized programs.

E2. One-factor sensitivity study (diagnostic, fixed N=256 streams).
    Hypothesis: any observed timing effect depends on a measurable set of
    timing, service and noise assumptions; it is not universal across models.
    Reference is the E1 combined-noise short-memory slice. Fourteen changes:
    host cost 15ns/1us; native T2 13ms/10s; link delay 1ms/5ms;
    benign load 0/16 jobs per period (reference 8); quota .6/2.4ms per 10ms
    period (reference 1.2ms); selected-phase error +/-0.5ms and +/-1ms.
    Both compilation variants, five policies, three arms in all 15 cells.
    Per stream: 15 x 2 x 5 x 3 = 450 executions.
    Full: 115,200 executions. Smoke: 900 executions (2 streams).
    450 predeclared selected-minus-random endpoint contrasts, reported with
    simultaneous bounded-mean intervals. This is NOT a full factorial sweep,
    a proof of robustness, or a test of every combination of adverse factors.

    Cost sweep: program length is fixed, so competing service CHANGES between
    host-cost cells. Completed service must match WITHIN the active-arm pair.
    Quota sweep: fixed offered work can be deferred; retain unfinished work.
    Larger budget does not imply more offered work (the 2.4ms cell is slack).
    Phase error is a controlled perturbation of the frozen 250us application
    phase, modulo the public 4ms window. It is not fresh training, a clock
    discovery algorithm, online adaptation or per-message stochastic jitter.
    The 13ms native T2 sensitivity is NOT a reproduction of the hardware-informed
    non-Markovian isotropic transfer map used in Q13/Q14.

E1/E2 inference is deliberately diagnostic. Their fixed N values are not a
power guarantee for the historical 0.3 percentage-point application effect.
They may produce wide intervals. No extension of N after examining outcomes;
any subsequent confirmation needs a separate, prospectively frozen protocol.
An interval is suppressed if quality is missing or completed active service
does not match. All-offered summaries are still reported. There is no sampling
until significant, deletion of unfavorable cells, or pooling with old results.

E3. Upstream compiler translation/parser gate (prepared integration check).
    Records compiler executable hashes/help, exact commands, stdout/stderr,
    elapsed time, generated iQoala hashes, optional FileCheck status and parser
    failures on three pinned upstream positive fixtures. Missing tools are an
    explicit BLOCKED state. No package installation or runtime mutation occurs.
    Native execution of compiled applications and equal-traffic evaluation are
    NOT implemented by this gate and remain required for the full comparison.

IMPORTANT EXPERIMENTS STILL TO IMPLEMENT, NOT HIDDEN INSIDE THIS NOTEBOOK

E4. A genuine adaptive network application after optimization.
    This is the highest-priority workload extension for a broad quantum-specific
    claim. Use a small entangled measurement-based/delegated protocol in which
    an earlier measurement determines a later non-Clifford instruction while
    unmeasured qubits remain live. Specify the information/operation dependency;
    check delayed preparation, rotation merging and correction-frame tracking.
    Do not assume every communication wait is unavoidable. Validate noiseless
    semantics for supported branches and multiple inputs; retain joint-state
    fidelity or the application's actual verification distribution. Then run
    equal-work random/even/fixed/selected timing under optimized FCFS, EDF,
    applicable published compilation, and exclusion/serialization alternatives.
    No such new native program, semantic proof or full runner is claimed here.

E5. Complete upstream compilation and any obtainable TUF comparison.
    After E3, integrate the actual matched application with the frontend and
    compiler, verify native semantics and run common traffic as described above.
    Freeze a SEPARATE comparison family; never append a new policy to the old
    six-contrast study. Obtain full TUF details before implementing an adapter.
    This is necessary for claims against those systems, not for citing them.

E6. Service-preserving held-out deployment-style traffic (conditional).
    Q16's historical planning point missed its declared timeliness gates.
    More N at that point does not establish a service-preserving attack.
    If that stronger claim remains a goal, develop a justified feasible design
    on planning-only data, freeze it, then use fresh independent renewal/jitter
    streams, unchanged deadline/quality targets and all-offered outcomes. Keep
    Q16 intact and name any changed assumptions. Do not sell E2 phase errors as
    this experiment or silently loosen service requirements after results.

E7. Resource-scale/generalization extensions (conditional on paper claims).
    Add multiple concurrent sessions, tighter physical memory, longer adaptive
    depth and separate peer delays if claiming general multi-tenant capacity.
    Admission failure/timeout/cleanup must be genuine outcomes, not discarded
    exceptions. Test each supported configuration in ideal semantics first.
    A large sweep is not needed merely to lengthen a simulation paper. Hardware
    validation is optional for a simulator-scoped claim; it is required for
    claims about actual device timings or deployed exploitability.

RECOMMENDED EXECUTION ORDER: SEQUENTIAL FAMILIES, EIGHT WORKERS TOTAL

1. Run smoke on the existing pending notebook and this new notebook. The new
   notebook defaults to MODE='smoke', WORKERS=8 and both new families. Both
   smoke families finish before either full diagnostic family can begin.
   Resolve integration failures before collecting final data; source changes
   require new versioned protocols and cannot be mixed with old checkpoints.
2. Complete the existing pending fixed-N timing/application studies. These
   directly address the stronger fixed-phase baselines and uncertain application
   contrasts. Do not launch another eight-worker job in the same allocation.
3. Run this notebook with MODE='full' for E1 then E2. It checks matching smokes,
   resumes hash-bound checkpoints, estimates approximate CPU/storage cost from
   smoke and exports a compact review ZIP. Raw traces stay on the server.
   Combined new full diagnostics: 176,640 native executions, excluding smoke.
4. Return the compact review ZIP and retain raw data for saved-raw replay.
   Use --command documentation below to replay before making paper claims.
5. Develop E4 and finish E5 while the independent existing runs execute. Build
   the compiler in its own environment, leaving the working P1 environment
   unchanged. Do not run the compiler build across all cores during native
   performance checks; record allocation/host environment. Reassess E6 only
   after the stronger application evidence and actual optimized workloads.

LOCAL PACKAGE / SERVER COMMANDS

Standalone notebook: P1_Additional_Studies.ipynb embeds this entire package.
Upload it to ~/P1 and use the existing native P1 kernel. No second upload is
needed. Default directory: ~/P1/additional-diagnostics-20261024-v1.
The original pending notebook remains a separate independent launch.

For users extracting the source ZIP instead:
  python source/test_suite.py
  python source/study_runner.py plan --family causal --mode full --folder results
  python source/study_runner.py run --family causal --mode smoke --folder results/causal-smoke --workers 8
  python source/study_runner.py run --family robustness --mode smoke --folder results/robustness-smoke --workers 8
  python source/study_runner.py run --family causal --mode full --folder results/causal-full --workers 8
  python source/study_runner.py run --family robustness --mode full --folder results/robustness-full --workers 8
  python source/study_runner.py replay --folder results/causal-full
  python source/study_runner.py replay --folder results/robustness-full
  python source/study_runner.py export --folder results
Supply --repo /path/to/pinned/qoala-sim if repository discovery needs help.
For E3: python source/compiler_gate.py --folder results/compiler-gate
Supply --tools-dir /path/to/compiler/bin if tools are not on PATH.

VALIDATION AND CURRENT STATUS

Local source/design/checkpoint/dependency tests pass; see LOCAL_VALIDATION.txt.
The local host lacks NetSquid, pydynaa, Qoala and NetQASM. Therefore NO native
smoke/full run has executed here. New native integration remains pending on the
server. The compiler gate cannot run translation without installed binaries.
There is no authenticated connection from this workspace to the Wahab server.
This package does not claim that E4/E5/E6/E7 are already implemented or run.

PRIMARY SOURCES

https://arxiv.org/html/2505.06162v1
https://github.com/SoftwareQuTech/qoala-mlir
https://github.com/SoftwareQuTech/qoala-mlir/blob/3a367ae374b623dee13f6bf3f2f2c332fc530496/docs/architecture/pipeline.md
https://research.tudelft.nl/en/publications/a-timeutility-function-for-scheduling-quantum-applications-2/
https://repository.tudelft.nl/record/uuid:fe6db5f5-2ed8-4ab5-9874-0bee5a906768
Maurer and Pontil (2009), arXiv:0907.3740, Theorem 4; same bounded-mean
interval implementation as the retained pending fixed-N study.
