"""sensor mode catalog

Revision ID: 4b8d2e1c9a70
Revises: 73866dc4fe27
Create Date: 2026-10-19 21:15:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8d2e1c9a70"
down_revision: str | None = "73866dc4fe27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sensor_mode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sat_id", sa.Integer(), nullable=False),
        sa.Column("mode_name", sa.String(length=32), nullable=False),
        sa.Column("resolution_m", sa.Float(), nullable=False),
        sa.Column("swath_km", sa.Float(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["sat_id"], ["satellite.sat_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sat_id", "mode_name", name="uq_sensor_mode_sat_name"),
    )
    op.create_index("idx_sensor_mode_res", "sensor_mode", ["resolution_m"], unique=False)

    op.create_table(
        "sensor_mode_correction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sat_id", sa.Integer(), nullable=False),
        sa.Column("mode_name", sa.String(length=32), nullable=True),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("corrected", sa.String(length=32), nullable=True),
        sa.Column("rule", sa.String(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["sat_id"], ["satellite.sat_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sm_corr_sat", "sensor_mode_correction", ["sat_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_sm_corr_sat", table_name="sensor_mode_correction")
    op.drop_table("sensor_mode_correction")
    op.drop_index("idx_sensor_mode_res", table_name="sensor_mode")
    op.drop_table("sensor_mode")
