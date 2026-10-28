E1 read-only raw audit bug fix v1.1
=================================

Fixed: the v1 checker evaluated missing[0] even when no traces were missing,
causing IndexError before the audit began. The v1.1 checker checks the list
only when nonempty. No experiment files, policies, checkpoint formats, or
scientific audit checks were changed.

Upload P1_E1_ReadOnly_Raw_Audit_Server_Fixed.ipynb to the server.
The first configuration cell defaults to:
RUN_DIR = Path('/home/rahmanms/P1/results/e1-20261025T040643708320Z')
WORKERS = 2
SAMPLE_FIRST = True
RUN_FULL = True

Choose Run All. Successful output: AUDIT RESULT: PASS_FULL_RAW and
~/P1/audits/E1_Raw_Audit_20261025/E1_Raw_Audit_Review_full.zip.

The auditor writes only in the separate audits directory; it reads the
original E1 run folder and source files without modifying them.

For a direct in-place fix in the previous notebook, replace:
    ensure(not missing, f'... {missing[0]} ...')
with:
    if missing:
        raise AuditError(f'Missing {len(missing)} raw traces. First missing: {missing[0]}')
