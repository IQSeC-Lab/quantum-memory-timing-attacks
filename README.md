# Quantum Memory Timing Attacks

Reproducibility artifact for **“Exploiting Quantum Memory Decoherence through Classical Scheduling in Shared Network Nodes.”**

This artifact studies how the release times of classical jobs affect quantum information stored at a shared network node. It includes the experiment implementations, frozen configurations and selections, retained compact results, statistical checks, editable TikZ figures, and replay evidence supporting the paper.

The instructions below follow the paper's experimental subsections and research questions. The central distinction is between **reproducing an analysis of existing results** and **executing new native simulations**. Neither operation constitutes a hardware experiment.

## 1. Choose a reproduction path

| Path | Purpose | Requirements | Entry point |
|---|---|---|---|
| Figure and table regeneration | Recreate the displays from retained, already computed analyses | Python and NumPy; TeX only for PDF compilation | `python scripts/reproduce.py figures` |
| Statistical reproduction (small download) | Recompute all fresh-study intervals and historical campaign/stream comparisons | Python, NumPy, SciPy; 0.55 MB statistical dataset | `python scripts/reproduce.py all` |
| Full compact-record checks (optional) | Recheck individual records, checkpoint hashes, pairing, and all-offered aggregates | Original compact archives, provided separately | `python scripts/reproduce.py all --full-records` |
| Native simulation | Execute the frozen workloads in Qoala/NetSquid and retain new raw and compact outputs | Pinned native environment, sufficient resources, appropriate predecessor evidence | Section 7 |
| Raw-to-compact replay | Check saved raw traces against compact outputs using the trace auditors | Full raw files and original directory structure | Section 8 |

**Recommended starting point:** regenerate the figures, then run the compact-record checks. The completed replay report is included. Inspecting every replay receipt additionally requires the optional original compact archives, but still does not require the server raw traces.

## 2. Paper-to-artifact map

RQ descriptions below summarize the questions stated in the supplied methodology. Subsection titles, rather than page numbers, identify the results because pagination can change.

| Paper subsection | Question or purpose | Retained inputs | Reproduction command | Principal outputs |
|---|---|---|---|---|
| Release Timing at Equal Competing Work | RQ1: Does calibrated release timing increase errors when completed competing work is equal? | Campaign and paired-trial statistical units | `python scripts/reproduce.py q12`; `python scripts/reproduce.py timing` | Campaign contrasts; 36 timing-grid contrasts; `analysis/figures/timing.tex` |
| Calibration-cost experiment in the methodology | RQ1: How does full calibration compare with cheaper, pooled, or fixed timing? | Campaign differences and calibration bits | `python scripts/reproduce.py e1` | Nine policy summaries and eight contrasts, including two designated primary contrasts |
| Transfer Across Memory Models | RQ2: Does the frozen timing remain harmful under the two specified isotropic profiles? | Paired campaign endpoint differences | `python scripts/reproduce.py q14` | Campaign-level transfer estimates and adjusted bootstrap intervals; `analysis/figures/transfer.tex` |
| Effect of Preparation Placement | RQ3: Can delaying preparation remove avoidable storage exposure? | Paired stream differences and retained mechanism records | `python scripts/reproduce.py application`; `python scripts/reproduce.py figures` | Primary application contrasts; `preparation.tex`; `storage_mechanism.tex` |
| Quantum Quality and Timely Service | RQ4: How do scheduling and exclusion affect both state quality and timely service? | Same primary application stream units | `python scripts/reproduce.py application` | Six primary contrasts; all-offered outcomes; `schedulers.tex`; `primary_contrasts.tex` |
| Noise Controls and Sensitivity | Diagnose memory exposure and sensitivity to individual settings | Independent diagnostic stream differences | `python scripts/reproduce.py diagnostics` | 140 noise-control and 450 sensitivity contrasts; `diagnostic_summary.tex` |
| Interference Using Only a Public Clock | Examine the weaker timing capability and its joint service criterion | Public-clock stream outcome counts | `python scripts/reproduce.py q16` | 192 stream summaries, 49,152 measured requests; `clock.tex` |

The default commands use the small statistical dataset. With `--full-records`, the timing and application commands both check their common original archive. The two paths are distinguished throughout: statistical reproduction does not by itself revalidate request records or checkpoint receipts.

### Study sizes and independent units

| Study | Independent units used for inference | Native executions or measured requests |
|---|---|---:|
| Original campaign timing | 32 campaigns per workload | 98,304 evaluation executions |
| Calibration cost, E1 | 32 campaigns | 73,728 evaluation executions |
| Fresh timing grid | 16,384 paired trials per workload | 655,360 executions |
| Memory-model transfer | 32 campaigns per workload | 98,304 executions |
| Earlier application study | 32 streams per condition | 7,680 application simulations |
| Primary application study | 8,192 paired streams per condition | 655,360 application simulations |
| Noise controls | 512 paired streams | 61,440 application simulations |
| Sensitivity | 256 paired streams | 115,200 application simulations |
| Public-clock study | 32 paired streams per arrival regime | 192 simulations; 49,152 measured requests |

An application simulation contains 12 offered quantum requests. Those requests are not 12 independent replications. Calibration, smoke, pilot, and recovery-validation records do not enter the fresh full-study inference.

## 3. Repository contents

| Location | Contents |
|---|---|
| `experiments/P1_Feedback_Closure/` | Frozen timing-grid and primary application implementations, their parent modules, selections, and source fingerprints |
| `experiments/P1_Additional_Studies/` | Noise-control and sensitivity implementations, including their exact parent package |
| `experiments/historical/q12/` | Original campaign timing implementation and metadata |
| `experiments/historical/calibration_cost/` | E1 calibration-cost implementation and recovery adapter |
| `experiments/historical/q13/` | Predecessor memory-model implementation and metadata used by Q14's prerequisite checks |
| `experiments/historical/q14/` | Frozen-policy transfer implementation and metadata |
| `experiments/historical/q16/` | Public-clock fresh planning implementation and metadata |
| `experiments/historical/q16_calibration/` | Retained predecessor calibration source and metadata |
| `experiments/historical/application_earlier/` | Earlier application implementation and metadata |
| `notebooks/` | Fourteen indexed notebook entry points and recovered original notebooks |
| `execution/` | Fresh-process execution adapters for the main and diagnostic families |
| `audit/` | Raw replay source, reference fingerprints, E1 compact and receipt cross-checks |
| `scripts/` | Portable analysis entry points and separate compact-record recomputations |
| `analysis/` | Retained analyses, figure data, TikZ, and regeneration scripts |
| `data/analysis/` | Nine small NumPy files containing every independent paired value used for the supported statistical comparisons |
| `data/ANALYSIS_MANIFEST.json` | Statistical-data checksums and source-archive identities |
| `data/archives/` | Optional original compact archives, excluded from normal Git commits |
| `outputs/` | Generated validation logs and historical reanalyses |
| `docs/` | Provenance, release readiness, and recorded environment information |

The original experiment packages retain their internal directory layouts and source bytes. Duplicate parent modules are intentional: each experiment binds its own version through recorded hashes. Do not deduplicate or reformat those modules before reproducing the results.

Original package notes describe their state at creation, including statements that execution was pending. The completed retained results and current validation report take precedence for execution status; those historical notes remain provenance records.

## 4. Setup and quick start

### Analysis environment

Use Python 3.10 for the closest match to the recorded environment. The recorded native interpreter is Python 3.10.14. The curated analysis workflow is also checked using the available drafting interpreter; see `docs/VALIDATION.json` for its exact version.

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-analysis.txt
python scripts/reproduce.py figures
```

Run commands from the repository root. `scripts/reproduce.py` also resolves the repository root when invoked by absolute path. Keep Python assertions enabled: do not use `python -O` or set `PYTHONOPTIMIZE`.

Figure generation needs no native simulator and no companion archive because its retained numerical inputs are already in `analysis/evidence/`. It creates seven figure sources, data tables, the primary-contrast table, and the diagnostic-summary table. `protocol.tex` is the schematic figure and is already included.

### Install the small statistical dataset

The companion `quantum-memory-timing-attacks-analysis-data.zip` contains nine files under `data/analysis/`, totaling about **0.55 MB**. Extract it **into this repository's root**, preserving the paths:

```bash
unzip /path/to/quantum-memory-timing-attacks-analysis-data.zip -d .
python scripts/check_data.py
python scripts/reproduce.py all
```

The `all` command runs 39 host-only engineering tests, recomputes all 632 fresh-study simultaneous intervals, checks historical campaign/stream comparisons and calibration selections, checks the included completed replay report, and regenerates figure/table data. Logs and timings are saved under `outputs/validation/`. A failure stops with a nonzero exit code.

**What is retained:** all independent paired differences for the 36 timing, 6 application, 140 noise-control, and 450 sensitivity contrasts; original campaign differences for the historical bootstrap analyses; Q12 own calibration bits needed to recover phase choices and E1's lower-budget/pooled selectors; earlier application primary stream differences; and all public-clock stream outcome counts. Float values remain float64, without rounding or subsampling.

**What is separate:** complete per-request compact records, checkpoint receipts, repeated program/specification metadata, execution logs, and raw traces. The small dataset reproduces statistical results; it is not a lossless replacement for those larger archives and does not independently repeat all record-integrity checks. Figure point values and latency summaries use the retained aggregate evidence under `analysis/evidence/`.

For the optional full-record workflow, place the twelve original ZIPs named in `data/MANIFEST.json` under `data/archives/`:

```bash
python scripts/check_data.py --full-records
python scripts/reproduce.py all --full-records
```

Those archives total about 251 MB and are not required for the default GitHub reproduction path. Their exact filenames and SHA-256 hashes remain documented. Publish or distribute them separately if offering complete checkpoint-level verification.

### Rebuild the small dataset from the original archives

The reduction is implemented in the repository, rather than provided as unexplained arrays. With the original archives in `data/archives/`, run:

```bash
python scripts/check_data.py --full-records
QMT_EXPORT_UNITS=1 python scripts/verify_main.py
QMT_EXPORT_UNITS=1 python scripts/verify_diagnostics.py
python scripts/build_analysis_dataset.py
python scripts/reproduce_analysis.py
```

The first two checkers export fresh-study values only after their full protocol, source, checkpoint, pairing, and accounting checks pass. The historical extractor reads committed batches and preserves campaign/stream units; it also retains the calibration bits needed for the original phase selectors. The final command checks that the extracted values reproduce the original estimates and intervals. `data/ANALYSIS_MANIFEST.json` records their hashes and the identities of all source archives.

### Notebooks for every RQ and experiment

Open `notebooks/00_Start_Here.ipynb` for complete retained-result reproduction. The [notebook guide](notebooks/README.md) indexes 13 additional notebooks covering campaign timing, calibration cost, the timing grid, memory transfer, preparation/storage, scheduling/deadlines, noise controls, sensitivity, public-clock interference, the earlier application study, predecessor memory models, public-clock calibration, and raw replay. Each numbered experiment notebook links to its exact Python implementation.

The 14 numbered notebooks are newly curated front ends, not claims that every original server notebook had already been included. Recovered original server notebooks are retained under `notebooks/originals/`; frozen-package notebooks remain at their checksum-bound paths. RQ3 and RQ4 share one application experiment. Native execution in the numbered notebooks is disabled by default; source/protocol checks remain in force when enabled. Historical original notebooks retain their original server paths and execution defaults.

### Figure and table outputs

The repository includes regeneration code and numerical figure/table inputs under `analysis/`. It excludes manuscript text, the LaTeX submission wrapper, references, and the paper PDF. Generated TikZ and table fragments can be copied into a separate manuscript project.

## 5. Reproduce each experimental subsection

### 5.1 Release Timing at Equal Competing Work — RQ1

**Purpose.** Separate the amount of competing CPU service from its release timing, and compare calibrated timing with both simple controls and a fixed-phase grid.

**Design.** The isolated workloads retain a plus state or teleport a uniformly selected Pauli eigenstate. Active timing-grid arms use four 30-operation jobs totaling 1.2 ms of completed competing service. The 20 arms are idle, random, even, learned, and 16 fixed phases. Calibration phases are frozen before evaluation; target outcomes never select a policy. Each new trial draws from the existing 32-policy ensemble for its workload.

```bash
python scripts/reproduce.py q12
python scripts/reproduce.py timing
python scripts/reproduce.py figures
```

**Checks and outputs.** The default commands recompute campaign-level means, bootstrap intervals, direction tests, calibration phase selections, and the 36 fresh timing intervals from their independent units. Reanalyses are saved under `outputs/analysis/`; display inputs are in `analysis/figures/data/`. The optional `--full-records` path additionally checks original batch/checkpoint hashes, protocols, pairing, completed service, and counts.

**Expected findings.** The original campaign teleportation increase over random timing is about 1.83 pp. In the fresh grid it is about 2.02 pp, with simultaneous interval [1.29, 2.75] pp; the increase over even timing is about 2.66 pp [1.93, 3.39]. The plus-state comparison with random remains inconclusive. Learned timing does not dominate every fixed phase. The maximum fixed-grid error is an evaluation oracle, not a policy selected before observing target outcomes.

### 5.2 Calibration cost — RQ1 supporting experiment

**Purpose.** Assess the value of campaign-specific full calibration relative to cheaper or shared choices. This study is distinct from both Q12 and the fresh timing grid.

**Design.** Nine teleportation policies receive 32 campaigns × 256 fresh target trials: full calibration, 8/32/64 observations per phase, leave-one-campaign-out pooled selection, fixed 0 ms, fixed 2 ms, random, and idle. The pooled selector excludes the held-out campaign. The two designated primary contrasts compare full calibration against pooled and fixed-2-ms timing.

```bash
python scripts/reproduce.py e1
# Optional, after obtaining the full compact archives:
python audit/e1/E1_Final_Receipt_Crosscheck.py \
  data/archives/E1_Raw_Audit_Review_full.zip \
  data/archives/E1_Review_complete_safe_resume.zip
```

**Checks and outputs.** The default command recomputes all eight campaign-level bootstrap contrasts and reconstructs full, lower-budget, and leave-one-out phase choices from the retained own calibration bits. The optional full-record command reconstructs the 73,728 evaluation records and checks their provenance. The separate receipt cross-check verifies 585 batches and 74,880 records including the 1,152-record pilot. Pilot observations do not enter evaluation inference.

**Interpretation.** Full calibration does not establish an advantage over the pooled policy. The fixed-2-ms comparison is favorable to full calibration in this experiment, but it does not establish superiority over all fixed phases. Lower-budget comparisons remain secondary. The E1 bootstrap procedure is retained; it is not replaced by the later empirical Bernstein method.

### 5.3 Transfer Across Memory Models — RQ2

**Purpose.** Test the existing selected phases under different accumulated memory channels, without retraining.

**Design.** The transfer crosses two workloads, two isotropic profiles, 32 campaigns, three arms, and 256 trials. Native memory noise is disabled and the accumulated isotropic channel is applied once before verification using the recorded storage duration. Profiles A and B use (T, n) = (13.0 ms, 1.67) and (13.2 ms, 1.63). They are two parameterizations from the same source.

```bash
python scripts/reproduce.py q14
python scripts/reproduce.py figures
```

**Checks and outputs.** The default command recomputes expected-infidelity and sampled-error campaign means and bootstrap intervals, together with mean storage differences, using the original campaign units and fixed seed. Expected-infidelity intervals use the original four-comparison adjustment; sampled-error intervals retain their original exploratory pointwise treatment. The optional full-record path additionally reads all 98,304 compact evaluation records and verifies batch hashes, pairing, phases, and service. `analysis/figures/transfer.tex` displays the retained endpoints.

**Expected findings.** All four expected-infidelity contrasts exclude zero. Plus-state increases are approximately 0.180 and 0.183 pp; teleportation increases are approximately 0.515 and 0.534 pp. Sampled teleportation errors resolve an increase, whereas sampled plus-state errors remain inconclusive. These results do not represent independent hardware replications or validate arbitrary noisy programs.

### 5.4 Effect of Preparation Placement — RQ3

**Purpose.** Determine whether an application can avoid holding its state while waiting for classical instructions.

**Design.** The application receives two rotation angles whose combined ideal operation preserves the input up to global phase. Early preparation stores the state across communication waits; deferred preparation receives both angles first and merges the rotations. The primary study crosses two parameter profiles, two program variants, five schedulers, and four release arms, with 8,192 paired streams and 12 offered quantum requests per stream.

```bash
python scripts/reproduce.py application
python scripts/reproduce.py figures
```

**Checks and outputs.** The default command recomputes the six primary stream-level intervals. Figure regeneration creates `preparation.tex` and `storage_mechanism.tex` from the retained aggregate and mechanism inputs. The optional full-record path verifies complete configuration inventories, private-input hashes, completed service, all offered outcomes, and identical active-arm infidelity under deferred preparation.

**Expected findings.** Under short memory, early preparation, and FCFS, the selected-minus-random infidelity estimate is about 0.421 pp, with interval [-0.0033, 0.8456] pp. It remains statistically unresolved. Deferred preparation removes the observed timing sensitivity within each tested configuration; this is not a population equivalence proof.

**Storage figure.** The retained mechanism inputs contain 98,304 matched requests per preparation variant. Panel (a) displays selected-minus-random storage differences; selected timing both lengthens and shortens intervals, with mean increase about 0.047 ms under early preparation. Every paired difference is zero after deferred preparation. Panel (b) uses 16,384 X+ requests per arm, grouped into 20 equal-count storage bins. No curve is fitted and no additional significance claim is made. The relationship reflects the modeled physics and does not independently establish causality.

### 5.5 Quantum Quality and Timely Service — RQ4

**Purpose.** Evaluate whether protection improves the service applications actually receive when both quantum quality and deadlines matter.

**Design.** The same primary application records compare FCFS, tenant round robin, numerical EDF, ready live-state priority, and native critical sections. Each of two epochs offers six quantum requests, eight benign jobs, and four competing jobs. Quantum deadlines are 5 ms and the observation horizon is 70 ms. The selected release schedule is frozen across schedulers.

```bash
python scripts/reproduce.py application
python scripts/reproduce.py figures
```

**Checks and outputs.** The supplied stream differences retain the original all-offered denominator of 12 requests. The default command recomputes their six primary intervals. Rate and latency displays use the retained aggregate summaries; `analysis/primary_contrasts.tex` and `schedulers.tex` regenerate those displays. The optional full-record path checks the request-level outcome partition, missing-snapshot accounting, and all-offered aggregates.

**Expected findings.** With short memory, early preparation, and selected timing, correct-and-timely completion is approximately 85.83% for EDF, 81.65% for FCFS, 83.91% for priority, and 68.20% for critical sections. Critical sections lower infidelity from about 16.18% to 6.89% but raise the late fraction from about 1.79% to 25.92%. EDF outperforms the tested priority policy on useful service. These are conditional comparisons under the tested workload and parameter settings.

### 5.6 Noise Controls and Sensitivity

**Purpose.** Check whether the application pattern is consistent with memory exposure and examine changes in individual settings.

**Design.** Noise controls cross ideal, memory-only, gate-only, and combined evolution with variants, policies, and release arms over 512 streams. Sensitivity examines 15 one-factor conditions over 256 streams: reference, host cost, memory lifetime, link latency, benign load, quota, and phase error. The source labels are `causal` and `robustness`; these directory names do not imply a causal proof or uniform robustness guarantee.

```bash
python scripts/reproduce.py diagnostics
python scripts/reproduce.py figures
```

**Checks and outputs.** The default command recomputes all 590 simultaneous intervals from the independent stream values. The optional full-record checker additionally verifies protocols, source identities, checkpoint hashes, pairing, completed work, and all-offered aggregates. The noise-control family has 140 contrasts; the sensitivity family has 450. Some noise-control contrasts are differences of paired differences and use the larger bounded range required by that estimand. `analysis/diagnostic_summary.tex` summarizes the entire families. Exact condition-level intervals remain in `analysis/evidence/causal_analysis.json` and `robustness_analysis.json`.

**Expected findings.** No interval in either family excludes zero. The combined-noise selected-minus-random interval is approximately [-8.51, 9.23] pp. Sensitivity point estimates change sign across settings. Ideal and gate-only zero estimates do not receive zero-width intervals. The studies do not establish equivalence or a stable advantage across all settings.

### 5.7 Interference Using Only a Public Clock

**Purpose.** Examine timing that receives only a periodic public clock, without the target epoch supplied in the main isolated experiments.

**Design.** The retained Q16 study has renewal and jittered periodic arrivals, three release arms, and 32 paired streams per arrival type. Each stream contributes 256 measured requests after 20 warmup requests. The fixed criterion requires at least 99% timely completion, less than 0.5 pp timely-service loss against each control, and more wrong-and-timely outcomes.

```bash
python scripts/reproduce.py q16
python scripts/reproduce.py figures
```

**Checks and outputs.** The default command reconstructs the six arm-level measured outcome counts, timely/wrong-and-timely rates, and paired stream variances from all 192 stream count vectors. This preserves all 49,152 measured requests in the arithmetic. Full request records and their integrity checks remain optional. `analysis/figures/clock.tex` marks the original thresholds; raw replay separately requires the original full Q16 archive.

**Expected findings.** Selected timely completion is approximately 98.901% and 98.889%, below 99%. Losses against even timing are about 0.696 and 0.586 pp, above the allowed 0.5 pp. The joint criterion is not met. This is a descriptive planning result, not a confirmatory weaker-capability attack and not proof that such an attack is impossible.

## 6. Statistical procedures and comparison rules

The fresh timing, application, noise-control, and sensitivity studies retain their frozen fixed-N empirical Bernstein procedure. For independent bounded units in [a,b], unbiased sample variance S², family size K, and alpha = 0.05, the radius is:

```text
sqrt(2*S²*log(4*K/alpha)/N) + 7*(b-a)*log(4*K/alpha)/(3*(N-1))
```

Paired endpoint differences usually lie in [-1,1]; differences of differences can lie in [-2,2]. The checker uses the appropriate span and clips endpoints to the permitted range. Family sizes are 36, 6, 140, and 450 respectively. Intervals are simultaneous within the declared family, not one global family across all studies.

The source is Maurer and Pontil, *Empirical Bernstein Bounds and Sample Variance Penalization*, COLT 2009, Theorem 4. The two-tail and family union bound yields the log(4K/alpha) term. The paper is available at https://www.learningtheory.org/colt2009/papers/012.pdf.

Earlier campaign and transfer analyses retain their original campaign-level bootstrap procedures, fixed random seeds, and endpoint-specific family adjustments. Q14 sampled-error intervals remain exploratory pointwise intervals; its expected-infidelity family is adjusted over four comparisons. E1 retains its own specified bootstrap analysis. The public-clock summaries are descriptive. Do not apply the later interval method retrospectively or pool these separate experiments.

Comparison rules are explicit in each checker: historical structured analyses use absolute tolerance 1e-12; main interval recomputations use 1e-12 and their count/aggregate checks use the recorded tolerances in `scripts/verify_main.py`; diagnostic recomputation uses 1e-12 for interval fields and exact reconstructed aggregate equality. Original raw replay uses the frozen trace checkers and exact equality of their resulting compact objects. Internal density/trace tolerances belong to those checkers and are retained in source.

## 7. Native environment and full simulation

### 7.1 Recorded environment

| Component | Recorded version |
|---|---|
| Python | 3.10.14 |
| Qoala | 2.1.0; clean tracked revision `31fa2c9699578d227736557f35cd01a134c4bbde` |
| NetSquid | 1.1.8 |
| pydynaa | 1.0.2 |
| NetQASM | 2.3.0 |
| NumPy | 1.26.4 |
| SciPy | 1.15.3 |

The native workflow uses Linux process and file-lock facilities. Obtain NetSquid through its authorized distribution and install the recorded compatible dependencies in a separate native environment. NetSquid is not bundled. The repository intentionally does not contain credentials or an installer that alters an existing simulator environment. Recorded module locations and source fingerprints are retained in the original receipts.

Set the path to an existing clean checkout in that environment:

```bash
export QOALA_REPO=/absolute/path/to/qoala-sim
python experiments/P1_Feedback_Closure/source/closure_runner.py preflight \
  --repo "$QOALA_REPO" --folder runs/environment
```

The preflight checks imports, source fingerprints, and the tracked Qoala revision; it does not execute a quantum simulation. A blocked preflight is not a successful smoke run. Do not bypass it by editing hashes or disabling assertions.

### 7.2 Fresh timing and application studies

Use the original runner to establish the smoke folders:

```bash
python experiments/P1_Feedback_Closure/source/closure_runner.py run \
  --family timing --mode smoke --workers 4 --repo "$QOALA_REPO" \
  --folder runs/main/timing-smoke
python experiments/P1_Feedback_Closure/source/closure_runner.py run \
  --family application --mode smoke --workers 4 --repo "$QOALA_REPO" \
  --folder runs/main/application-smoke
```

For the full runs, the supplied external execution adapter bounds worker lifetime without editing the experiment package. It rechecks the original smoke, repeats it with fresh workers, requires exact compact agreement, and then runs or resumes full checkpoints:

```bash
python execution/main_recovery/recover_native.py run \
  --package experiments/P1_Feedback_Closure --results runs/main \
  --family timing --mode full --workers 4 --repo "$QOALA_REPO"
python execution/main_recovery/recover_native.py run \
  --package experiments/P1_Feedback_Closure --results runs/main \
  --family application --mode full --workers 4 --repo "$QOALA_REPO"
```

Expected full outputs are `runs/main/timing-full/` and `application-full/`, including `protocol.json`, `COMPLETE.json`, `ANALYSIS.json`, raw traces, compact records, and checkpoints. There are 2,048 timing chunks and 8,192 application streams. Both families produce 655,360 native execution records.

### 7.3 Noise controls and sensitivity

The diagnostic adapter uses the unchanged additional-study package and its parent:

```bash
python execution/diagnostic_recovery/recover_additional.py run \
  --package experiments/P1_Additional_Studies --results runs/additional \
  --family causal --mode full --workers 4 --repo "$QOALA_REPO"
python execution/diagnostic_recovery/recover_additional.py run \
  --package experiments/P1_Additional_Studies --results runs/additional \
  --family robustness --mode full --workers 4 --repo "$QOALA_REPO"
```

The adapter establishes/checks the original smoke and its fresh-worker equivalent before the full study. Expected full outputs contain 512 noise-control streams and 256 sensitivity streams, yielding 61,440 and 115,200 native records. Source/protocol checks and failure preservation remain active.

### 7.4 Historical native entry points and prerequisites

The statistical workflow above is portable and has been executed during curation; the separate full compact-record checks are also retained. Historical native workflows additionally bind predecessor records, calibration selections, or audited source identities. They cannot all be started from an empty folder with one generic command.

| Study | Native entry point | Required predecessor evidence |
|---|---|---|
| Q12 | `experiments/historical/q12/source/q12.py run` | Pinned environment; pilot and calibration are generated by its own workflow |
| E1 | `experiments/historical/calibration_cost/source/p1_e1.py run` (the notebook uses its retained safe-resume adapter) | Original Q12 run and matching Q12 source, including own calibration records |
| Q14 | `experiments/historical/q14/source/q14.py run` | Matching Q12/Q13 run folders and their completed raw-audit folders |
| Earlier application | `experiments/historical/application_earlier/source/app_cli.py` | Its frozen application design and pinned environment |
| Q16 fresh planning | `experiments/historical/q16/source/q16_fresh.py run` | Retained Q16 calibration folder satisfying its exact binding |

Use `--help` on those entry points for their complete arguments. For example, Q12 accepts `--repo`, `--run-dir`, `--mode pilot|full`, and `--workers`; Q14 also accepts `--q12-run`, `--q13-run`, `--q12-audit`, and `--q13-audit`. E1 accepts `--project`, `--q12-run`, `--q12-code`, `--repo`, `--run-dir`, `--mode`, and `--workers`. Q16 fresh planning requires `--calibration-dir`.

A review ZIP intentionally omits some original raw files, so it is not automatically sufficient to satisfy predecessor raw-audit gates. Q14's historical predecessor audit records also retain run-path bindings. These checks are not disabled in this artifact. Native reruns requiring those original records remain conditional on obtaining them and validating the recorded identities; compact reanalysis does not have that requirement.

### 7.5 Resource planning and recovery

No universal full-run runtime or RAM requirement is asserted. The source packages contain recorded execution costs and the original recovery receipts; a new machine's native smoke is the appropriate estimate. Use at most the available CPU allocation and account for simultaneous worker memory. Four workers are a conservative starting choice, not a measured memory guarantee.

The four fresh families generate roughly 39.8 GB of compressed raw files in the retained server run. Storage needed for a new run also includes compact records, logs, checkpoints, and interrupted partial traces. Worker-seconds in replay reports measure audit processing, not simulation wall time.

After interruption, resume the same family, code, run root, and design. Valid checkpoint hashes are rechecked; failed partial files are preserved. Changing concurrency does not change N or seeds. Do not run two coordinators for the same family against the same folder, delete checkpoints to evade a mismatch, extend N after inspecting effects, or mix smoke outcomes into full inference.

## 8. Completed raw replay and its scope

The supplied `P1_Full_Raw_Replay_LATEST_review.zip` reports `PASS_FULL_RAW_TO_COMPACT_REPLAY`:

| Family | Audit jobs | Records |
|---|---:|---:|
| Timing | 2,048 | 655,360 |
| Application | 8,192 | 655,360 |
| Noise controls (`causal`) | 512 | 61,440 |
| Sensitivity (`robustness`) | 256 | 115,200 |
| **Total** | **11,008** | **1,487,360** |

```bash
python scripts/reproduce.py receipts
```

The default command checks the included completed report and its stated coverage. To check the inventory, status, and identity consistency of all retained receipts, use `python scripts/reproduce.py receipts --full-records` after obtaining the optional original archives. Neither command reads absent server raw traces or repeats the raw audit.

The recovered raw auditor source is in `audit/raw_replay/`. Its command interface is:

```bash
python audit/raw_replay/audit_raw.py run \
  --main-root /path/to/original/main-run-root \
  --additional-root /path/to/original/additional-run-root \
  --output /path/to/separate/new-audit-output --mode full --workers 4
```

Each original root must contain the appropriate package and its `results/<family>-full/` subdirectories, including raw, compact, protocol, completion, and checkpoint files. The fixed reference binds the reviewed studies; this command is not a generic auditor for arbitrarily new runs. A sample audit is a diagnostic only, not full coverage.

**Source-provenance qualification:** the recovered auditor source's calculated identity differs from the identity in the completed server receipts. Both identities and the source hashes are recorded in `docs/RELEASE_STATUS.md`. The completed receipts remain intact, but the exact final server auditor source must be recovered before claiming the release contains that exact executed auditor. Do not relabel the recovered source or change fingerprints to conceal the difference.

The full replay excludes historical Q12/Q13/Q14/Q16 and E1. Their checks and receipts are separate. Across all studies, raw-to-compact agreement verifies record processing and consistency; it does not independently simulate noisy quantum evolution, validate a memory model against hardware, or demonstrate deployed attacker access.

## 9. Troubleshooting

| Symptom | Action |
|---|---|
| Missing `data/analysis/*.npz` | Extract the small analysis-data ZIP at repository root, then run `scripts/check_data.py` |
| Missing optional `data/archives/*.zip` | Use the default statistical workflow, or obtain the original ZIPs listed in `data/MANIFEST.json` for `--full-records` |
| NetSquid/Qoala import failure during native preflight | Activate the authorized pinned native environment; analysis-only commands do not need these imports |
| Source/protocol checksum mismatch | Restore the exact frozen file or inspect the mismatch; do not rewrite the expected hash |
| Missing predecessor raw-audit folder | Obtain the matching original evidence or use compact reproduction; a review archive is not full raw evidence |
| Native worker allocation failure | Preserve logs, reduce concurrent workers, and resume through the fresh-process adapter |
| Full analysis stops before PASS | Inspect `outputs/validation/<task>.log`; partial output is not a completed validation |
| Default reanalysis succeeds but full-record checks cannot find archives | The lightweight dataset and optional full archives serve different verification scopes; see Section 4 |
| Diagnostic interval contains zero | This is an expected scientific outcome, not a software failure |

## 10. Citation, license, and release status

The MIT license applies to original artifact code. Retained third-party notices remain applicable, including the Qoala-derived components and bundled TeX files. NetSquid is obtained separately under its own terms. See `THIRD_PARTY_NOTICES.md`.

Use the paper's final bibliographic entry when available. No DOI, publication status, release tag, or public artifact URL is invented in this working package.

This is a curated release candidate. `docs/VALIDATION.json` records checks actually executed; `docs/RELEASE_STATUS.md` lists remaining release qualifications. A public release should preserve the companion-data checksums and identify a fixed code version so readers can reproduce the artifact associated with the paper.
