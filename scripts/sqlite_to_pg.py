"""把 RS-Service 的 SQLite 数据复制到 PostgreSQL。

用法：
    RS_DATABASE_URL=postgresql+psycopg://... \
        .venv/bin/python scripts/sqlite_to_pg.py

默认读取 ~/.local/share/rs-service/rs.db；目标表先 TRUNCATE，再按外键顺序写入，
并重置 id 序列。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from sqlalchemy import Boolean, MetaData, create_engine, text

from rs_service.settings import settings

TABLES = [
    "data_source",
    "satellite",
    "satellite_alias",
    "sensor",
    "sensor_mode",
    "sensor_mode_correction",
    "tle_record",
    "tle_current",
    "sync_run",
    "sync_item_error",
    "sync_state",
    "aoi",
    "pass_window",
    "coverage_snapshot",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite",
        default=os.path.expanduser("~/.local/share/rs-service/rs.db"),
        help="源 SQLite 文件",
    )
    parser.add_argument("--url", default=None, help="目标 PostgreSQL URL")
    args = parser.parse_args()

    url = args.url or settings.sqlalchemy_url()
    if url.startswith("sqlite"):
        sys.exit("目标仍是 SQLite；请设置 RS_DATABASE_URL 指向 PostgreSQL")
    if not os.path.exists(args.sqlite):
        sys.exit(f"源 SQLite 不存在：{args.sqlite}")

    source = sqlite3.connect(args.sqlite)
    source.row_factory = sqlite3.Row
    engine = create_engine(url, future=True)

    metadata = MetaData()
    metadata.reflect(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE TABLE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        )
        for name in TABLES:
            table = metadata.tables[name]
            columns = [row[1] for row in source.execute(f"pragma table_info({name})")]
            rows = [dict(row) for row in source.execute(f"select * from {name}")]
            bool_columns = {
                column.name
                for column in table.columns
                if isinstance(column.type, Boolean)
            }
            converted = []
            for row in rows:
                item = {}
                for key, value in row.items():
                    if key in bool_columns and value is not None:
                        value = bool(value)
                    item[key] = value
                converted.append(item)
            if converted:
                conn.execute(table.insert(), converted)
            print(f"{name}: {len(converted)}")
            if set(columns) != {column.name for column in table.columns}:
                print(f"  ! 列不一致：sqlite={sorted(columns)}")

        for name in TABLES:
            if "id" not in metadata.tables[name].columns:
                continue
            sequence = conn.execute(
                text("select pg_get_serial_sequence(:table, 'id')"), {"table": name}
            ).scalar()
            if sequence:
                conn.execute(
                    text(
                        f"select setval(:seq, coalesce((select max(id) from {name}), 1))"
                    ),
                    {"seq": sequence},
                )

    print("done")


if __name__ == "__main__":
    main()
