# Release readiness

The curated analysis workflow reproduces the retained results without the native quantum simulator. See `VALIDATION.json` for actual checks and their timings.

## Completed during curation

- Recovered and organized the frozen main, diagnostic, historical, and execution-recovery sources.
- Recomputed all 36 timing-grid, 6 primary application, 140 noise-control, and 450 sensitivity intervals.
- Recomputed original Q12 campaign, Q14 transfer, Q16 public-clock, E1 calibration-cost, and earlier application analyses from compact outcomes.
- The prior full-record curation checked the completed fresh replay report and all 11,008 receipts covering 1,487,360 records. The default small dataset includes its completed report; full receipts remain separate.
- Checked E1's separate raw-audit receipt bindings against compact checkpoints.
- Regenerated the current paper figures/tables under analysis/; the manuscript folder is excluded from this release.

## Exact final raw-auditor source remains to be recovered

Completed server receipt auditor identity:

```text
2f2548544f6a0873fb55aed0ee88e0faa13158b6a88aeaa73eea2c02c358e7dd
```

Recovered source identity, calculated from its Python source hashes and `REVIEWED_REFERENCE.json` using its own identity formula:

```text
defbaae8ad8ef8bb04e27e34908ab8a9884a8a4b4ea80ab6f76365799d0d3cc6
```

The recovered source's own `AUDITOR_SOURCE_HASHES.json` matches its files. It is a coherent recovered version, but it is not established as the exact version behind the completed server receipts. A matching receipt inventory is not sufficient to close that source-provenance gap.

Copy `scripts/export_executed_auditor.py` to the original server and run it there:

```bash
python export_executed_auditor.py --project "$HOME/P1"
```

It searches source directories, computes the identity, and exports only a version matching the completed receipt identity. It does not run simulations, replay raw traces, or modify the experiment folders. No match produces an explicit failure rather than a guessed export.

## Native execution qualification

No new native simulations are run during this curation because NetSquid/Qoala are unavailable in the drafting environment. Native commands are grounded in recovered CLI implementations; their relocated environment still needs a native preflight and smoke run. The original retained server execution and audit receipts are supplied separately.

Historical Q14 native execution requires the exact predecessor runs and raw audits, including their path bindings. Review archives alone do not contain all original raw traces. The artifact preserves those checks and documents the resulting prerequisites rather than silently weakening them.

## Publication packaging

The code ZIP excludes the paper folder and all full result archives. The companion analysis-data ZIP supplies nine NumPy files totaling about 0.55 MB. These preserve the independent statistical units used for the supported comparisons at full precision.

Default commands recompute statistics, calibration choices, public-clock stream rates, and figure/table data. They do not repeat request-level, checkpoint, or raw-trace integrity checks. The existing full-record commands are available with --full-records when the original archives are supplied separately. Their original filenames and hashes remain in data/MANIFEST.json.

The default artifact is small enough to distribute with the code. Full compact archives and native raw traces can be placed in a separate archival deposit if that verification scope is offered. No external GitHub publication, release tag, DOI, or new clean-environment native reproduction is claimed.
