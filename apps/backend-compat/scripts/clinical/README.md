# Clinical Study Scripts

## 1) Silent study (DECIDE-AI style)

```bash
python scripts/clinical/run_silent_study.py \
  --replay-dir ~/.probeplanner/replay \
  --out-dir ./benchmark_out/silent_study \
  --manual-override-csv ./manual_override_log.csv
```

Outputs:
- `silent_study_cases.csv`
- `failure_cards.csv`
- `silent_study_summary.md`

## 2) Interventional analysis (non-inferiority)

```bash
python scripts/clinical/analyze_interventional_trial.py \
  --csv ./trial_data.csv \
  --out-dir ./benchmark_out/interventional \
  --noninferiority-margin -0.05
```

Required CSV columns:
- `arm` (`manual` or `manual_plus_system`)
- `safetyPass` (`0/1`)

Optional:
- `planningTimeSec`
- `coverageRate`

