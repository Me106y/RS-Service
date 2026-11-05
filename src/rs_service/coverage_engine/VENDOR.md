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
2. No other engine logic is changed.  The vendor modules still import each
   other by their historical top-level names, so `coverage_engine/__init__.py`
   adds `vendor/` to `sys.path` once.

Do not edit vendored files beyond the profile entry above; put integration
logic in `adapter.py` and keep a note here when a change is unavoidable.
