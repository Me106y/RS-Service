# Coverage engine golden fixtures

Source: `satellite-coverage-backend` 0.1.0-rc.1 (source commit
`c04878fea6812c94ff325a619d54f9915bb88774`), owned by the SIRIAgent project.

- `data/platform/siriagent_110_catalog.json` — frozen 110-satellite catalog.
- `data/test_data/siriagent_110_snapshot.tle` — matching TLE snapshot.
- `data/geo/*.json` — Zhejiang city boundaries used by the examples.
- `expected_task1.json` — output of the reference implementation for
  `examples/task1.json`, used by `tests/test_coverage_golden.py`.

Do not edit the data files. If the reference engine is upgraded on purpose,
regenerate `expected_task1.json` with the new release and note it in the PR.
