"""Read back the reports that :func:`write_report` left on disk.

目录约定：``<root>/<task>/<run_id>/{plan.json,report.html,meta.json}``。
列表只读 ``meta.json``——``plan.json`` 动辄几百 KB，几十份全读会把接口拖到几秒。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .renderer import report_root, task_key

MAX_LIMIT = 500


def _base(root: Path | None) -> Path:
    return Path(root) if root else report_root()


def siriagent_root(root: Path) -> Path:
    """``reports/imaging-plan`` 的上一级上一级就是 SIRIAgent 仓库根。"""

    return Path(root).parents[1]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(siriagent_root(root)).as_posix()
    except ValueError:
        return str(path)


def iter_run_dirs(root: Path) -> Iterator[Path]:
    base = _base(root)
    if not base.is_dir():
        return
    for task_dir in sorted(base.iterdir()):
        if not task_dir.is_dir() or task_dir.name == "latest":
            continue
        for run_dir in sorted(task_dir.iterdir()):
            if run_dir.is_dir():
                yield run_dir


def read_meta(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def find_run_dir(root: Path | None, run_id: str) -> Path | None:
    """按 run_id 找目录：报告改成分任务子目录后，不能再拼 ``<root>/<run_id>``。"""

    value = str(run_id or "")
    if not value or "/" in value or "\\" in value or ".." in value:
        return None
    for run_dir in iter_run_dirs(_base(root)):
        if run_dir.name == value:
            return run_dir
    return None


def list_reports(
    root: Path | None = None,
    *,
    operation: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """按生成时间倒序列出历史报告；``operation`` 接受 task1..task5 或旧 slug。"""

    base = _base(root)
    wanted = task_key(operation) if operation else None
    runs: list[dict[str, Any]] = []
    by_task: dict[str, int] = {}
    for run_dir in iter_run_dirs(base):
        meta = read_meta(run_dir)
        if meta is None:
            continue
        task = str(meta.get("task") or task_key(meta.get("operation")))
        by_task[task] = by_task.get(task, 0) + 1
        if wanted and task != wanted:
            continue
        report_path = run_dir / "report.html"
        plan_path = run_dir / "plan.json"
        runs.append(
            {
                **meta,
                "task": task,
                "dir": str(run_dir),
                "has_report": report_path.is_file(),
                "relative_report": _relative(report_path, base),
                "relative_plan": _relative(plan_path, base),
            }
        )
    runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    capped = max(1, min(int(limit or 200), MAX_LIMIT))
    return {"runs": runs[:capped], "total": len(runs), "by_task": by_task}


__all__ = ["find_run_dir", "iter_run_dirs", "list_reports", "read_meta"]
