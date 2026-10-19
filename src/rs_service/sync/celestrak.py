from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

USER_AGENT = "rs-service/0.1 (SIRIAgent ephemeris; contact: local)"


@dataclass
class FetchResult:
    ok: bool
    http_status: int | None = None
    items: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    bytes: int = 0
    not_modified: bool = False


def fetch_active(
    base_url: str,
    *,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
    proxy: str | None = None,
) -> FetchResult:
    """下载 CelesTrak GROUP=active 的 OMM/JSON；单次请求，不做内部重试。"""
    params = {"GROUP": "active", "FORMAT": "json"}
    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        proxy=proxy,
    )
    try:
        response = client.get(
            base_url,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        body = response.content
        if response.status_code == 304:
            return FetchResult(ok=True, http_status=304, not_modified=True, bytes=len(body))
        if response.status_code == 403:
            text = response.text[:500]
            code = "not_modified" if "not updated" in text.lower() else "forbidden"
            return FetchResult(
                ok=False,
                http_status=403,
                error_code=code,
                error_message=text or "403 Forbidden",
                bytes=len(body),
                not_modified=code == "not_modified",
            )
        if response.status_code != 200:
            return FetchResult(
                ok=False,
                http_status=response.status_code,
                error_code=f"http_{response.status_code}",
                error_message=response.text[:500],
                bytes=len(body),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            return FetchResult(
                ok=False,
                http_status=200,
                error_code="invalid_json",
                error_message=str(exc),
                bytes=len(body),
            )
        if not isinstance(payload, list):
            return FetchResult(
                ok=False,
                http_status=200,
                error_code="invalid_payload",
                error_message="expected a JSON array",
                bytes=len(body),
            )
        return FetchResult(ok=True, http_status=200, items=payload, bytes=len(body))
    except httpx.HTTPError as exc:
        return FetchResult(
            ok=False,
            error_code="network_error",
            error_message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if owns_client:
            client.close()


def fetch_many_norad(
    base_url: str,
    sat_ids: set[int] | list[int],
    *,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
    proxy: str | None = None,
    max_workers: int = 4,
) -> FetchResult:
    """逐星回退：GROUP=active 被 403 限制时，按 CATNR 拉取种子卫星。"""
    import concurrent.futures
    import time

    ids = sorted({int(item) for item in sat_ids})
    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        proxy=proxy,
    )
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    total_bytes = 0

    def one(sat_id: int) -> tuple[list[dict[str, Any]], str | None, int]:
        try:
            response = client.get(
                base_url,
                params={"CATNR": str(sat_id), "FORMAT": "json"},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            if response.status_code != 200:
                return [], f"{sat_id}:http_{response.status_code}", len(response.content)
            payload = response.json()
            if not isinstance(payload, list):
                return [], f"{sat_id}:invalid_payload", len(response.content)
            time.sleep(0.15)
            return payload, None, len(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            return [], f"{sat_id}:{type(exc).__name__}", 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            for result_items, error, size in pool.map(one, ids):
                items.extend(result_items)
                total_bytes += size
                if error:
                    errors.append(error)
    finally:
        if owns_client:
            client.close()

    if not items:
        return FetchResult(
            ok=False,
            error_code="all_failed",
            error_message="; ".join(errors[:20]) or "no items",
            bytes=total_bytes,
        )
    return FetchResult(
        ok=True,
        http_status=200,
        items=items,
        bytes=total_bytes,
        error_message="; ".join(errors[:20]) if errors else None,
    )


def fetch_for_sync(
    base_url: str,
    sat_ids: set[int] | list[int],
    *,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
    proxy: str | None = None,
) -> FetchResult:
    """先拉 active；若被 403/not-modified 限制，回退到逐星 CATNR。"""
    result = fetch_active(base_url, timeout=timeout, client=client, proxy=proxy)
    if result.ok and not result.not_modified:
        return result
    if result.not_modified or result.http_status == 403:
        fallback = fetch_many_norad(
            base_url,
            sat_ids,
            timeout=timeout,
            client=client,
            proxy=proxy,
        )
        if fallback.ok:
            fallback.error_message = (
                f"active 403, per-NORAD fallback; {fallback.error_message or ''}"
            ).strip()
        return fallback
    return result
