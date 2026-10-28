# Notebook guide

Start with [00_Start_Here.ipynb](00_Start_Here.ipynb). Native execution is disabled by default.

| Notebook | Experiment | Implementation |
|---|---|---|
| [01_RQ1_Campaign_Timing.ipynb](01_RQ1_Campaign_Timing.ipynb) | RQ1 — Original campaign timing | `experiments/historical/q12/source/q12.py` |
| [02_RQ1_Calibration_Cost.ipynb](02_RQ1_Calibration_Cost.ipynb) | RQ1 — Calibration cost and simpler policies | `experiments/historical/calibration_cost/source/p1_e1_resume.py` |
| [03_RQ1_Timing_Grid.ipynb](03_RQ1_Timing_Grid.ipynb) | RQ1 — Fresh timing grid at equal competing work | `execution/main_recovery/recover_native.py` |
| [04_RQ2_Memory_Transfer.ipynb](04_RQ2_Memory_Transfer.ipynb) | RQ2 — Frozen-policy transfer across memory models | `experiments/historical/q14/source/q14.py` |
| [05_RQ3_Preparation_and_Storage.ipynb](05_RQ3_Preparation_and_Storage.ipynb) | RQ3 — Preparation placement and recorded storage | `execution/main_recovery/recover_native.py` |
| [06_RQ4_Quality_and_Deadlines.ipynb](06_RQ4_Quality_and_Deadlines.ipynb) | RQ4 — Scheduling protection, quality, and deadlines | `execution/main_recovery/recover_native.py` |
| [07_Noise_Controls.ipynb](07_Noise_Controls.ipynb) | Noise controls — Ideal, memory-only, gate-only, and combined | `execution/diagnostic_recovery/recover_additional.py` |
| [08_Sensitivity.ipynb](08_Sensitivity.ipynb) | Sensitivity — Fifteen one-factor configurations | `execution/diagnostic_recovery/recover_additional.py` |
| [09_Public_Clock.ipynb](09_Public_Clock.ipynb) | Public-clock interference — Weaker timing access | `experiments/historical/q16/source/q16_fresh.py` |
| [10_Earlier_Application.ipynb](10_Earlier_Application.ipynb) | Earlier application factorial study | `experiments/historical/application_earlier/source/app_cli.py` |
| [11_Predecessor_Memory_Models.ipynb](11_Predecessor_Memory_Models.ipynb) | Predecessor Q13 — Memory-model prerequisite | `experiments/historical/q13/source/q13.py` |
| [12_Public_Clock_Calibration.ipynb](12_Public_Clock_Calibration.ipynb) | Public-clock calibration — Retained predecessor | `experiments/historical/q16_calibration/source/q16_calibration.py` |
| [13_Raw_Replay.ipynb](13_Raw_Replay.ipynb) | Completed raw replay — Receipts and exact-source qualification | `audit/raw_replay/audit_raw.py` |

The numbered notebooks are newly curated entry points to the retained implementations. Original server notebooks are preserved separately under `originals/` and inside frozen packages. RQ3 and RQ4 share one native application study; do not rerun it solely because two notebooks reference it.

## Recovered original notebooks

- [P1_Additional_Studies.ipynb](originals/P1_Additional_Studies.ipynb)
- [P1_E1_Calibration_Cost_Server.ipynb](originals/P1_E1_Calibration_Cost_Server.ipynb)
- [P1_E1_Safe_Resume_After_Pilot.ipynb](originals/P1_E1_Safe_Resume_After_Pilot.ipynb)
- [P1_Full_Raw_Replay.ipynb](originals/P1_Full_Raw_Replay.ipynb)
- [P1_Independent_Application_Validation_Fixed.ipynb](originals/P1_Independent_Application_Validation_Fixed.ipynb)
- [P1_Qoala_Q12_Server.ipynb](originals/P1_Qoala_Q12_Server.ipynb)
- [P1_Qoala_Q13_Server.ipynb](originals/P1_Qoala_Q13_Server.ipynb)
- [P1_Qoala_Q14_Frozen_Transfer_Server.ipynb](originals/P1_Qoala_Q14_Frozen_Transfer_Server.ipynb)
- [P1_Qoala_Q14_Raw_Audit_Server.ipynb](originals/P1_Qoala_Q14_Raw_Audit_Server.ipynb)
- [P1_Resume_Additional_Diagnostics.ipynb](originals/P1_Resume_Additional_Diagnostics.ipynb)
- [P1_Resume_After_MemoryError.ipynb](originals/P1_Resume_After_MemoryError.ipynb)
