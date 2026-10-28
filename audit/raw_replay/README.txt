P1 FULL RAW-TRACE REPLAY

Run P1_Full_Raw_Replay.ipynb in the existing native P1 environment. The notebook
embeds this audit code and exact protocol/checkpoint fingerprints from both
reviewed compact archives. It reads the four existing full-study raw folders.
It does not run a simulation, retrain a policy, change N, or edit experiment data.

Default roots:
  ~/P1/feedback-native-20261024-v1
  ~/P1/additional-diagnostics-20261024-v1
Default output:
  ~/P1/raw-replay-20261025-v1

The replay verifies original source fingerprints, exact reviewed protocols and
completion receipts, every expected checkpoint's reviewed checksum, raw/compact
SHA256s, complete record inventories, and exact raw-to-compact agreement after
invoking the frozen trace auditors. Timing inputs/public plans are regenerated
from the frozen design; application programs, configurations, readiness, policy
decisions, outputs and density validity are checked by the original raw auditor.

These are separate post-execution checks using existing checkers. They are NOT
an independently implemented quantum simulator, a proof of the noise model, a
new hardware experiment, or validation of deployed attacker access. Previously
reviewed compact summaries and 42+590 intervals were independently recalculated
in the drafting workspace. This replay links their compact inputs to saved raw.

Main-study raw files contain gzip JSON arrays; additional-study files contain
gzip JSONL. Both are read record-by-record. Worker processes are fresh per raw
chunk/stream. Four audit workers are the default; eight are allowed if memory
and I/O capacity permit. Expected full raw inventory: 2,048 timing chunks,
8,192 application streams, 512 causal streams, 256 robustness streams. Full
records: 655,360 + 655,360 + 61,440 + 115,200 = 1,487,360. Smoke records are not
included. About 39.8 GB of compressed full raw files remain on the server.

SAMPLE_FIRST=True performs first/middle/last chunks per selected study as an
integration diagnostic. It is NOT a full audit or a statistical sample-based
certification. RUN_FULL=True then audits all expected chunks. Sample receipts
can be reused only after their raw and compact hashes are checked again.

The tool writes per-chunk PASS receipts only after exact replay. On interruption,
rerun the same notebook/output path. Cached PASS receipts require identical
auditor/reference/job bindings AND rehashed unchanged raw/compact files. Missing
files, modified protocols/sources, mismatches, truncation and extra records stop
the audit; no partial coverage is promoted to full PASS. Preserve failure logs.
Use a distinct audit output directory when splitting studies across sessions.

The final full-REPORT.json must read PASS_FULL_RAW_TO_COMPACT_REPLAY for every
selected study. Return the small raw-replay-20261025-v1-review.zip. Raw traces
stay on the server. If all four studies are selected, expect all 11,008 jobs.
This audit excludes historical Q12/Q13/Q14/Q16, whose existing records remain
untouched. No study folder receives an audit output or edited source file.

Local validation uses six host-only engineering tests for streaming formats,
truncation, inventory, path containment, checksum failures and missing raw.
No production raw data or full server replay has been executed locally.
