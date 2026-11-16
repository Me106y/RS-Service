# Vendored coverage engine

This directory is a copy of the regional coverage / selection engine from
`satellite-coverage-backend` 0.1.0-rc.1 (source commit
`c04878fea6812c94ff325a619d54f9915bb88774`), owned by the SIRIAgent project.

## Why vendored

The engine is pure Python (numpy / pandas / shapely / pyproj / skyfield /
sgp4 / PuLP-CBC) and has no framework coupling.  Keeping a copy under
`rs_service.coverage_engine` makes the planning path self-contained and keeps
the golden tests reproducible without a second service.

## Layout

- `vendor/*.py` — coverage / geometry / metrics / selection modules.
- `vendor/siriagent_skill/` — request normalization, execution facade,
  formatter and the original JSON entry point.
- `adapter.py` (added by RS-Service) — builds the engine working directory from
  the RS-Service database and calls the facade.

## Deliberate changes

1. `vendor/siriagent_skill/schemas.py` adds the `rs_service_db_v1` profile.
   RS-Service does not ship the 110-satellite frozen catalog; per-satellite
   geometry is injected through `satellite_profile_overrides`.
2. `vendor/satellite_loader.py` extends `parse_tle_text` to accept an OMM JSON
   payload (`{"satellites": [...]}`) in addition to 3LE text.  RS-Service's
   `tle_record` stores CelesTrak OMM JSON and has no TLE lines, so the adapter
   writes OMM to the workspace and the loader builds `EarthSatellite` through
   `EarthSatellite.from_omm`.  The 3LE branch is unchanged.
3. `vendor/siriagent_skill/facade.py` adds an optional `window_filter` hook to
   `execute_request`, applied after opportunity windows are computed and before
   selection. RS-Service uses it for the Open-Meteo cloud filter; `run_skill`
   passes `None`, so the original entry point is unchanged.
4. The vendor modules still import each other by their historical top-level
   names, so `coverage_engine/__init__.py` adds `vendor/` to `sys.path` once.

Do not edit vendored files beyond the three entries above; put integration logic
in `adapter.py` and keep a note here when a change is unavoidable.

## 4. Progress hook (v2.0.0)

`facade.execute_request` accepts an optional `progress` callback
`(stage, label, current, total)` and forwards per-satellite fractions from
`regional_opportunities.compute_regional_opportunities` (which already had a
`progress` parameter). `run_skill` passes `None`, so the original entry point is
unchanged. RS-Service uses it to fill `GET /plans/{id}.progress`.

## 5. Time-limited greedy (v2.0.0)

`greedy_select_mrt` accepts `time_limit_s`; on timeout it stops the forward
selection and reverse-delete, returns the current best set and status
`time_limited`. `run_regional_mrt_selection` exposes `greedy_time_limit_s` and
the facade passes `request.greedy_time_limit_s` (default 60s). Without it the
168h / 180-candidate pool can spend minutes in greedy search.

## 6. Parallel opportunity computation (v2.0.0)

`facade._compute_opportunities` splits the satellite list into contiguous
groups, runs `compute_regional_opportunities` per group in a thread pool
(`RS_OPPORTUNITY_WORKERS`, default 8), and merges windows by offsetting
`sat_index`. With a fixed `start_utc` the merged result is identical to the
serial path; the 53-satellite / 72h opportunity stage drops from ~4.9s to
~1.3s on the Xihu district and keeps golden tests unchanged. A per-worker
`timescale` avoids shared Skyfield state.
