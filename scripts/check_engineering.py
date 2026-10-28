"""Run the retained host-only engineering suites with portable package paths."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ,
           P1_FROZEN_PACKAGE=str(root / 'experiments/P1_Feedback_Closure'),
           P1_ADDITIONAL_PACKAGE=str(root / 'experiments/P1_Additional_Studies'))
for path in ['experiments/P1_Feedback_Closure/source/offline_tests.py',
             'experiments/P1_Additional_Studies/source/test_suite.py',
             'audit/raw_replay/test_audit.py',
             'execution/main_recovery/test_recovery.py',
             'execution/diagnostic_recovery/test_recovery.py']:
    subprocess.run([sys.executable, str(root / path)], cwd=root, env=env, check=True)
print('PASS_ENGINEERING: 39 host-only tests; no native quantum simulations executed.')
