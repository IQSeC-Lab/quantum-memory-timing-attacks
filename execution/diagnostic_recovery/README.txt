RECOVER ADDITIONAL P1 DIAGNOSTICS AFTER MEMORYERROR

Use P1_Resume_Additional_Diagnostics.ipynb for the failed causal/robustness run.
It is different from P1_Resume_After_MemoryError.ipynb, which recovers the first
timing/application study. Both recovery notebooks can run in independent server
allocations. Each keeps its own original run root and output formats.

Observed failure: the additional-suite worker exhausted memory while building
a native quantum processor, then could not allocate memory to format/save the
exception or close gzip output. This establishes allocation failure, not a
precise leak source. The printed stream 282 is an ordered result; more streams
may have produced checkpoints before process-pool shutdown.

The original executor reuses native workers across many streams. This external
adapter instead starts one fresh spawned process per original stream, with at
most WORKERS such processes alive. Ordered map submission is bounded to WORKERS
outstanding items. The implementation uses APIs available in Python 3.10 and
does not require max_tasks_per_child (introduced later).

No file under P1_Additional_Studies is edited. The original worker, complete
within-stream pairing, settings, seeds, sample sizes, comparison families,
source/protocol checks, checkpoint hashes, raw auditor and analysis are reused.
The changed execution adapter has its own source hashes and provenance receipt.
No checksum or protocol restriction is disabled. No synthetic data is used in
the native workflow; the small engineering tests use labeled temporary fixtures.

DEFAULTS AND SERVER USE

1. Upload the one self-contained notebook into ~/P1 and use the existing native
   P1 environment. Keep the original run and every saved checkpoint:
     ~/P1/additional-diagnostics-20261024-v1/
2. Keep FAMILY='all', MODE='full', WORKERS=8 to recover causal and robustness.
3. Run All. The notebook prefers the same Python interpreter recorded for the
   original run and checks native module provenance against previous preflight.
4. The adapter resumes/checks existing smokes, repeats selected smoke families
   in a separate recovery validation folder, replays their raw traces, and
   requires exact compact agreement with the saved original smoke outcomes.
5. It then resumes each original full folder. The original checkpoint reader
   verifies raw/compact hashes and runs only missing streams. A failed stream
   without a valid checkpoint is retried. The original worker preserves any
   prior .partial raw file under failures before retrying it.

Fresh smoke validation is excluded from full-study inference. Every scheduled
full stream still contains the complete frozen cell/policy/arm inventory.
Native smoke equivalence remains to be checked on the server. Local host-process
engineering checks do not establish quantum outcomes or device memory behavior.

INDEPENDENT SESSIONS

To split work across separate eight-core allocations, use FAMILY='causal' in
one session and FAMILY='robustness' in the other. Original study folders,
recovery status/log/validation folders and review ZIP names are distinct.
Do not run 'all' alongside a subset, a second copy of the same family, or the
original additional launch cell against the same stage directory. Per-stage
RUN.lock checks remain active. The first timing/application recovery uses a
different run root and can run in its own independent allocation.

MEMORY AND LIMITATIONS

Fresh processes limit retention across streams. They do not guarantee that
eight concurrent streams fit every session memory allowance. If necessary,
increase allocated RAM or set WORKERS=4 (or fewer) and resume without changing
N or deleting completed results. Causal streams contain 120 simulations;
robustness streams contain 450 simulations. If one fresh stream alone exceeds
the available memory, a separate within-stream execution/storage adaptation is
needed; changing worker lifetime alone is insufficient.

Each worker prints PID and peak RSS in Linux KiB. Available cgroup metadata is
recorded without asserting that hierarchy-root values are the session limit.
The original analyzer is unchanged. Its per-stream data retention and final
latency arrays may require memory; any later parent-analysis error is distinct
from the current worker error and must not be solved by omitting requests.

Return the family-specific compact review ZIP after completion. It retains
protocol/checkpoint/source receipts, summaries and hashes. Large raw/failure
files stay on the server for independent full saved-raw replay. The additional
studies remain diagnostic rather than powered confirmation of small effects.

VALIDATION

Six engineering tests exercise bounded ordered map, distinct worker processes,
MemoryError propagation, executor restoration, resuming the original runner
without rerunning complete streams, preservation of interrupted partial raw
files, source integrity, and fail-closed native preflight. Temporary mock
records are lifecycle fixtures, not quantum simulations or paper evidence.
Native recovery has not been executed in the drafting environment.
