"""remove the nine GEO satellites from the catalog

2026-10-21：GEO 凝视足迹模型未实现，过境接口会把常驻可见算成整窗“过境”，
从数据库移除 9 颗 GEO（GF-4/GF-13 01/02、LuTan-4 01A、FY-2G/H、FY-4A/B/C）。
种子文件同步改为 102 颗 LEO；本迁移只负责已有库的数据清理。

Revision ID: d2f6a1c3b4e5
Revises: 4b8d2e1c9a70
Create Date: 2026-10-21
"""

from __future__ import annotations

from alembic import op

revision = "d2f6a1c3b4e5"
down_revision = "4b8d2e1c9a70"
branch_labels = None
depends_on = None

GEO_IDS = (40367, 41194, 41882, 43491, 46610, 48808, 55912, 57624, 67246)


def upgrade() -> None:
    ids = ", ".join(str(item) for item in GEO_IDS)
    # 先删引用 satellite 的子表，再删主表；没有子表的表也保留，幂等。
    for table in (
        "sensor_mode_correction",
        "sensor_mode",
        "sensor",
        "pass_window",
        "satellite_alias",
        "tle_current",
        "tle_record",
    ):
        op.execute(f"DELETE FROM {table} WHERE sat_id IN ({ids})")
    op.execute(f"DELETE FROM satellite WHERE sat_id IN ({ids})")


def downgrade() -> None:
    # 数据删除不可逆：如需恢复，使用 /tmp/geo_removed_backup.json 或 git 历史中的种子。
    pass
