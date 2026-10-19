from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import typer
from sqlalchemy import func, select

from .db.models import Satellite, Sensor, SyncRun, SyncState, TleCurrent
from .db.seed import DEFAULT_SEED_PATH, seed_catalog
from .db.session import init_db, session_scope
from .settings import settings
from .sync.runner import run_sync

app = typer.Typer(no_args_is_help=True, add_completion=False, help="RS-Service CLI")


def _dump(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def init(
    db: str | None = typer.Option(None, "--db", help="数据库 URL；默认 ~/.local/share/rs-service/rs.db"),
    seed: str | None = typer.Option(None, "--seed", help="111 颗卫星种子 JSON 路径"),
) -> None:
    """建表并写入卫星目录种子。"""
    init_db(db)
    with session_scope(db) as session:
        result = seed_catalog(session, path=seed)
    _dump({"ok": True, "db": db or settings.sqlalchemy_url(), **result})


@app.command()
def seed(
    db: str | None = typer.Option(None, "--db"),
    seed_path: str | None = typer.Option(None, "--seed"),
) -> None:
    """只写入/更新卫星目录，不建表。"""
    with session_scope(db) as session:
        result = seed_catalog(session, path=seed_path)
    _dump({"ok": True, **result})


@app.command()
def sync(
    db: str | None = typer.Option(None, "--db"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只抓取与解析，不写库"),
    force: bool = typer.Option(False, "--force", help="忽略熔断，人工排障用"),
    max_retries: int = typer.Option(settings.sync_max_retries, "--max-retries"),
    retry_delay: int | None = typer.Option(None, "--retry-delay", help="覆盖退避秒数，测试用"),
) -> None:
    """执行一次同步：最多 3 次重试，失败当天停止。"""
    delays = [retry_delay] * (max_retries + 1) if retry_delay is not None else settings.retry_delays()
    with session_scope(db) as session:
        outcome = run_sync(
            session,
            dry_run=dry_run,
            force=force,
            max_retries=max_retries,
            retry_delays=delays,
        )
    _dump(asdict(outcome))
    if outcome.status in {"failed", "abandoned"}:
        raise typer.Exit(code=1)


@app.command()
def status(db: str | None = typer.Option(None, "--db")) -> None:
    """查看卫星、TLE 与同步状态。"""
    with session_scope(db) as session:
        sat_count = session.scalar(select(func.count()).select_from(Satellite)) or 0
        sensor_count = session.scalar(select(func.count()).select_from(Sensor)) or 0
        tle_count = session.scalar(select(func.count()).select_from(TleCurrent)) or 0
        max_age = session.scalar(select(func.max(TleCurrent.age_hours)))
        last_run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
        state = session.scalar(select(SyncState))
        payload = {
            "db": db or settings.sqlalchemy_url(),
            "satellites": sat_count,
            "sensors": sensor_count,
            "tle_current": tle_count,
            "tle_max_age_hours": max_age,
            "last_run": (
                {
                    "status": last_run.status,
                    "attempt_no": last_run.attempt_no,
                    "started_at_utc": last_run.started_at_utc,
                    "finished_at_utc": last_run.finished_at_utc,
                    "http_status": last_run.http_status,
                    "inserted": last_run.inserted,
                    "unchanged": last_run.unchanged,
                    "failed": last_run.failed,
                    "error_code": last_run.error_code,
                }
                if last_run
                else None
            ),
            "sync_state": (
                {
                    "last_attempt_at": state.last_attempt_at,
                    "last_success_at": state.last_success_at,
                    "last_status": state.last_status,
                    "consecutive_failures": state.consecutive_failures,
                    "attempts_today": state.attempts_today,
                    "retry_count_today": state.retry_count_today,
                    "circuit_open_until": state.circuit_open_until,
                }
                if state
                else None
            ),
        }
    _dump(payload)


@app.command("serve-api")
def serve_api(
    host: str = typer.Option(settings.api_host, "--host"),
    port: int = typer.Option(settings.api_port, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """启动 REST API（只绑 localhost）。"""
    import uvicorn

    from .api.app import create_app

    uvicorn.run(create_app(), host=host, port=port, reload=reload)


@app.command("serve-mcp")
def serve_mcp(
    host: str = typer.Option(settings.mcp_host, "--host"),
    port: int = typer.Option(settings.mcp_port, "--port"),
) -> None:
    """启动 MCP server（Streamable HTTP，只绑 localhost）。"""
    from .mcp.server import run_server

    run_server(host=host, port=port)


@app.command()
def info() -> None:
    """打印当前配置与种子路径。"""
    _dump(
        {
            "version": "0.1.0",
            "db_url": settings.sqlalchemy_url(),
            "db_path": str(settings.resolved_db_path()),
            "seed_path": str(DEFAULT_SEED_PATH),
            "celestrak": settings.celestrak_base_url,
            "max_retries": settings.sync_max_retries,
            "retry_delays": settings.retry_delays(),
            "api": f"{settings.api_host}:{settings.api_port}",
            "mcp": f"{settings.mcp_host}:{settings.mcp_port}",
        }
    )


if __name__ == "__main__":
    app()
