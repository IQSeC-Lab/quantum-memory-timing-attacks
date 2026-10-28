P1 MEMORY-ERROR RECOVERY -- EXTERNAL EXECUTION ADAPTER

The reported error occurred after 685 reported completed timing chunks out of
2,048. That is chunks, not individual simulations. Additional chunks may have
finished before the process pool shut down. The original runner will verify
the actual raw/compact checkpoint hashes and report its authoritative count.

The traceback establishes an allocation failure in a worker, followed by another
allocation failure while writing its failure report. Reading a small JSON file
was the failure point; it is not proof that that file caused memory exhaustion.
Long-lived workers currently execute many simulator chunks. Memory retained
across chunks is a plausible cause, not a demonstrated allocation profile.

RECOVERY METHOD

This adapter launches each original chunk in a fresh spawned Python process.
At most WORKERS native workers run concurrently, and each child is joined before
its manager returns the result. A small thread pool coordinates those processes.
The method works with Python 3.10; it does not rely on the Python 3.11-only
ProcessPoolExecutor max_tasks_per_child constructor argument.

The files under P1_Feedback_Closure are never edited. The original main(),
worker(), source checks, protocol checks, checkpoints, seeds, paired inputs,
sample sizes, comparison families and raw audits remain in use. The executor
substitution is explicit external orchestration, recorded by a separate source
hash/provenance receipt. No protocol mismatch is suppressed and no completed
checkpoint is copied into a changed experiment.

Before resuming a selected full family, the recovery notebook:
1. Verifies the existing package and native pinned environment.
2. Resumes/hash-checks the original smoke folder.
3. Repeats that smoke in a separate recovery validation folder with fresh workers.
4. Requires exact agreement with every original smoke compact record. Each run
   also passes the unchanged native raw auditor. A mismatch stops recovery.
5. Resumes the original full folder, reusing only hash-verified checkpoints.

New smoke validation records do not enter the full-study inference. Historical
Q12/Q13/Q14/Q16 are not run. Native full results still need independent saved-raw
replay before updating paper claims. Exact compact agreement is not a second
independent simulation of noisy physics.

SERVER USE

Upload P1_Resume_After_MemoryError.ipynb to ~/P1 and use the existing native P1
kernel. It needs the original extracted run already present at:
  ~/P1/feedback-native-20261024-v1/
Keep that RUN_ROOT. Do not delete results, checkpoints or failed partial files.
The notebook embeds the recovery adapter; no additional upload/install is needed.

Defaults: WORKERS=8, MODE='full', FAMILY='all'. Both fresh smoke checks complete
before either full family is resumed. Previously verified full chunks are reused.
The original runtime package and original native_dispatch.py remain unchanged.
Use this recovery notebook instead of restarting the old launch cell unchanged.

PARALLEL SESSIONS

If Wahab grants an independent eight-core allocation to EACH session, separate
sessions may run independent studies concurrently with eight workers each.
That corrects the earlier assumption of a single shared eight-core allocation.
Account for each session's memory allocation as well as its CPU allocation.

For these two frozen studies, use FAMILY='timing' in one session and
FAMILY='application' in another, with the same original RUN_ROOT. Their study
output directories, recovery logs/locks, validation folders and export archives
are distinct. Do not also run FAMILY='all', the old launcher, or another copy of
the same family against those folders simultaneously. The original per-study
RUN.lock continues to protect duplicate stage execution.

The independent P1_Additional_Studies notebook has a different run root and can
use another independently allocated session. It still has its own long-lived
executor; a similar lifetime change may be appropriate before large native runs.
This recovery adapter specifically supports the frozen closure_runner study.

MEMORY LIMITATIONS AND DIAGNOSTICS

Fresh processes bound lifetime across chunks; they do not make memory unlimited.
If eight simultaneous fresh chunks exceed the session's memory allocation,
increase the session's memory allocation or set WORKERS=4 and resume the same
run. This changes concurrency, not N, seeds, conditions or retained checkpoints.
If a SINGLE fresh chunk exceeds available memory, worker recycling alone is
insufficient; retain logs and inspect that peak before redesigning chunk storage.

Each fresh worker prints its PID and Linux peak RSS (KiB). Available cgroup
metadata is recorded, with an explicit warning that hierarchy-root values may
not be the actual session limit. No memory-leak cause is claimed solely from
these measurements. The parent final analyzer is unchanged; if its complete
dataset aggregation later exceeds memory, that is a separate issue requiring
an output-equivalent streaming analysis adapter, not a reason to drop data.

The controller log, status and exact exception are printed and linked after a
failure. Compact archives preserve selected-family checkpoints/protocols and
source provenance; native raw/failure records remain on the server with hashes.

LOCAL VALIDATION

Five engineering tests cover fresh process identity, futures/wait behavior,
MemoryError propagation, restoration of the original executor, reuse of valid
checkpoints without rerunning work, unchanged source hashes and fail-closed
native preflight. Their mock lifecycle fixtures are NOT quantum experiment data.
No native quantum recovery run has been executed in the drafting workspace.
The server's fresh smoke comparison is the required native integration check.
