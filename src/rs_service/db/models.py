from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Satellite(Base):
    __tablename__ = "satellite"

    sat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    norad_alpha5: Mapped[str | None] = mapped_column(String(8))
    intl_designator: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_zh: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(8))
    category: Mapped[str | None] = mapped_column(String(32))
    object_type: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str | None] = mapped_column(String(16))
    orbit_regime: Mapped[str | None] = mapped_column(String(16))
    launch_date: Mapped[str | None] = mapped_column(String(16))
    is_domestic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    metadata_source: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)

    __table_args__ = (Index("idx_sat_status_cat", "status", "category"),)


class SatelliteAlias(Base):
    __tablename__ = "satellite_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sat_id: Mapped[int] = mapped_column(ForeignKey("satellite.sat_id"), nullable=False)
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    alias_norm: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (UniqueConstraint("alias_norm", "source", name="uq_alias_norm_source"),)


class DataSource(Base):
    __tablename__ = "data_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32))
    base_url: Mapped[str | None] = mapped_column(String(512))
    query_template: Mapped[str | None] = mapped_column(String(512))
    auth_type: Mapped[str | None] = mapped_column(String(32))
    credential_ref: Mapped[str | None] = mapped_column(String(128))
    min_interval_s: Mapped[int | None] = mapped_column(Integer)
    daily_budget: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    terms_url: Mapped[str | None] = mapped_column(String(512))
    last_success_at: Mapped[str | None] = mapped_column(String(32))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class TleRecord(Base):
    __tablename__ = "tle_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sat_id: Mapped[int] = mapped_column(ForeignKey("satellite.sat_id"), nullable=False)
    epoch_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    line1: Mapped[str | None] = mapped_column(String(128))
    line2: Mapped[str | None] = mapped_column(String(128))
    omm_json: Mapped[str | None] = mapped_column(Text)
    checksum_ok: Mapped[bool | None] = mapped_column(Boolean)
    mean_motion: Mapped[float | None] = mapped_column(Float)
    inclination_deg: Mapped[float | None] = mapped_column(Float)
    eccentricity: Mapped[float | None] = mapped_column(Float)
    raan_deg: Mapped[float | None] = mapped_column(Float)
    argp_deg: Mapped[float | None] = mapped_column(Float)
    mean_anomaly_deg: Mapped[float | None] = mapped_column(Float)
    bstar: Mapped[float | None] = mapped_column(Float)
    mean_motion_dot: Mapped[float | None] = mapped_column(Float)
    mean_motion_ddot: Mapped[float | None] = mapped_column(Float)
    ephemeris_type: Mapped[int | None] = mapped_column(Integer)
    element_set_no: Mapped[int | None] = mapped_column(Integer)
    rev_at_epoch: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"), nullable=False)
    fetched_at_utc: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)

    __table_args__ = (
        UniqueConstraint("sat_id", "epoch_utc", "source_id", name="uq_tle_sat_epoch_source"),
        Index("idx_tle_sat_epoch", "sat_id", "epoch_utc"),
        Index("idx_tle_epoch", "epoch_utc"),
    )


class TleCurrent(Base):
    __tablename__ = "tle_current"

    sat_id: Mapped[int] = mapped_column(ForeignKey("satellite.sat_id"), primary_key=True)
    tle_id: Mapped[int] = mapped_column(ForeignKey("tle_record.id"), nullable=False)
    epoch_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    age_hours: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)


class Sensor(Base):
    __tablename__ = "sensor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sat_id: Mapped[int] = mapped_column(ForeignKey("satellite.sat_id"), nullable=False)
    sensor_name: Mapped[str | None] = mapped_column(String(128))
    sensor_type: Mapped[str | None] = mapped_column(String(32))
    pan_resolution_m: Mapped[float | None] = mapped_column(Float)
    ms_resolution_m: Mapped[float | None] = mapped_column(Float)
    tir_resolution_m: Mapped[float | None] = mapped_column(Float)
    swath_km: Mapped[float | None] = mapped_column(Float)
    along_track_km: Mapped[float | None] = mapped_column(Float)
    fov_deg: Mapped[float | None] = mapped_column(Float)
    bands_json: Mapped[str | None] = mapped_column(Text)
    imaging_modes_json: Mapped[str | None] = mapped_column(Text)
    revisit_days_nominal: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512))
    raw_spec: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)

    __table_args__ = (Index("idx_sensor_sat", "sat_id"),)


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_source.id"))
    started_at_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at_utc: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    next_retry_at: Mapped[str | None] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer)
    requested: Mapped[int | None] = mapped_column(Integer)
    inserted: Mapped[int | None] = mapped_column(Integer)
    updated: Mapped[int | None] = mapped_column(Integer)
    unchanged: Mapped[int | None] = mapped_column(Integer)
    failed: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_sync_src_time", "source_id", "started_at_utc"),)


class SyncItemError(Base):
    __tablename__ = "sync_item_error"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_run.id", ondelete="CASCADE"), nullable=False
    )
    sat_id: Mapped[int | None] = mapped_column(Integer)
    attempt_no: Mapped[int | None] = mapped_column(Integer)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at_utc: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)


class SyncState(Base):
    __tablename__ = "sync_state"

    source_id: Mapped[int] = mapped_column(ForeignKey("data_source.id"), primary_key=True)
    last_attempt_at: Mapped[str | None] = mapped_column(String(32))
    last_success_at: Mapped[str | None] = mapped_column(String(32))
    last_status: Mapped[str | None] = mapped_column(String(24))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open_until: Mapped[str | None] = mapped_column(String(32))
    next_scheduled_at: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)


class Aoi(Base):
    __tablename__ = "aoi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str | None] = mapped_column(String(16))
    adcode: Mapped[str | None] = mapped_column(String(16))
    geom_wkt: Mapped[str | None] = mapped_column(Text)
    min_lat: Mapped[float | None] = mapped_column(Float)
    min_lon: Mapped[float | None] = mapped_column(Float)
    max_lat: Mapped[float | None] = mapped_column(Float)
    max_lon: Mapped[float | None] = mapped_column(Float)
    area_km2: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(64))
    datum: Mapped[str | None] = mapped_column(String(16))
    accuracy: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)


class PassWindow(Base):
    __tablename__ = "pass_window"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sat_id: Mapped[int] = mapped_column(ForeignKey("satellite.sat_id"), nullable=False)
    aoi_id: Mapped[int] = mapped_column(ForeignKey("aoi.id"), nullable=False)
    tle_id: Mapped[int] = mapped_column(ForeignKey("tle_record.id"), nullable=False)
    algo_version: Mapped[str] = mapped_column(String(16), nullable=False)
    aos_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    los_utc: Mapped[str] = mapped_column(String(32), nullable=False)
    tca_utc: Mapped[str | None] = mapped_column(String(32))
    max_elev_deg: Mapped[float | None] = mapped_column(Float)
    aos_az_deg: Mapped[float | None] = mapped_column(Float)
    los_az_deg: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    min_range_km: Mapped[float | None] = mapped_column(Float)
    sun_elev_deg: Mapped[float | None] = mapped_column(Float)
    is_daylight: Mapped[bool | None] = mapped_column(Boolean)
    footprint_hit: Mapped[bool | None] = mapped_column(Boolean)
    coverage_ratio: Mapped[float | None] = mapped_column(Float)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "sat_id", "aoi_id", "aos_utc", "algo_version", name="uq_pass_sat_aoi_aos_algo"
        ),
        Index("idx_pass_aoi_aos", "aoi_id", "aos_utc"),
        Index("idx_pass_sat_aos", "sat_id", "aos_utc"),
        Index("idx_pass_tle", "tle_id"),
    )


class CoverageSnapshot(Base):
    __tablename__ = "coverage_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aoi_id: Mapped[int] = mapped_column(ForeignKey("aoi.id"), nullable=False)
    window_start_utc: Mapped[str | None] = mapped_column(String(32))
    window_end_utc: Mapped[str | None] = mapped_column(String(32))
    sat_set_hash: Mapped[str | None] = mapped_column(String(64))
    grid_spacing_deg: Mapped[float | None] = mapped_column(Float)
    k_min: Mapped[int | None] = mapped_column(Integer)
    temporal_coverage_pct: Mapped[float | None] = mapped_column(Float)
    area_coverage_pct: Mapped[float | None] = mapped_column(Float)
    mrt_s: Mapped[float | None] = mapped_column(Float)
    art_s: Mapped[float | None] = mapped_column(Float)
    algo_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso, nullable=False)
