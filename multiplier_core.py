"""Pure Sub2API multiplier parsing, reporting, and HTTP helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

import httpx


GROUPS_PATH = "/api/v1/admin/groups/all"
TIE_EPSILON = 1e-9


class Sub2APIError(RuntimeError):
    """A safe, user-facing error that never contains the API key."""


@dataclass(frozen=True)
class Sub2APIInstanceConfig:
    name: str
    base_url: str
    admin_api_key: str

    @property
    def cache_key(self) -> str:
        return f"{self.name}\x00{self.base_url}"


@dataclass(frozen=True)
class Sub2APIGroupRate:
    group_id: str
    name: str
    platform: str
    status: str
    rate_multiplier: float
    model_names: tuple[str, ...] = ()
    peak_rate_enabled: bool = False
    peak_rate_multiplier: float | None = None
    dynamic_rate_enabled: bool = False
    dynamic_rate_markup: float | None = None


@dataclass(frozen=True)
class MultiplierReport:
    groups: tuple[Sub2APIGroupRate, ...]
    minimum_multiplier: float | None
    minimum_groups: tuple[Sub2APIGroupRate, ...]
    skipped_groups: int = 0


def build_groups_url(base_url: str, include_inactive: bool = False) -> str:
    """Validate a base URL and append the Sub2API admin groups endpoint."""

    value = str(base_url or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise Sub2APIError("Sub2API 域名无效，请填写 http:// 或 https:// 地址")
    query = "include_inactive=true" if include_inactive else "include_inactive=false"
    return f"{value}{GROUPS_PATH}?{query}"


async def fetch_groups(
    instance: Sub2APIInstanceConfig,
    *,
    include_inactive: bool = False,
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> tuple[Sub2APIGroupRate, ...]:
    """Fetch and normalize group multipliers from one Sub2API instance."""

    url = build_groups_url(instance.base_url, include_inactive)
    headers = {"x-api-key": instance.admin_api_key, "Accept": "application/json"}
    owns_client = client is None
    request_client = client or httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds)

    try:
        try:
            response = await request_client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise Sub2APIError("请求超时") from exc
        except httpx.RequestError as exc:
            raise Sub2APIError("网络请求失败，请检查域名和网络连接") from exc

        if response.status_code in {401, 403}:
            raise Sub2APIError("管理员 API Key 无效或权限不足")
        if response.status_code == 404:
            raise Sub2APIError("管理接口不存在，请检查 Sub2API 版本和域名")
        if response.status_code >= 400:
            raise Sub2APIError(f"Sub2API 返回 HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise Sub2APIError("Sub2API 返回的不是合法 JSON") from exc

        raw_groups = _extract_group_list(payload)
        if raw_groups is None:
            raise Sub2APIError("Sub2API 返回中没有找到分组数据")

        normalized: list[Sub2APIGroupRate] = []
        for raw_group in raw_groups:
            group = _normalize_group(raw_group)
            if group is not None:
                normalized.append(group)
        return tuple(normalized)
    finally:
        if owns_client:
            await request_client.aclose()


def build_report(groups: Iterable[Sub2APIGroupRate]) -> MultiplierReport:
    """Build the minimum-rate view using the stored base group multiplier."""

    ordered = tuple(groups)
    if not ordered:
        return MultiplierReport((), None, ())

    minimum = min(group.rate_multiplier for group in ordered)
    minimum_groups = tuple(
        group for group in ordered if abs(group.rate_multiplier - minimum) <= TIE_EPSILON
    )
    return MultiplierReport(ordered, minimum, minimum_groups)


def format_multiplier(value: float) -> str:
    """Format a multiplier with at most four decimal places."""

    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def format_report(instance_name: str, report: MultiplierReport) -> str:
    """Render one instance report without exposing its URL or API key."""

    lines = [f"【{instance_name}】Sub2API 模型倍率", f"分组数量：{len(report.groups)}"]
    if not report.groups:
        lines.append("没有找到可用的倍率分组。")
        return "\n".join(lines)

    lines.append("")
    for group in report.groups:
        status_suffix = " | 停用" if group.status != "active" else ""
        details = [
            f"- {group.name} | {group.platform}{status_suffix} | 基础倍率：{format_multiplier(group.rate_multiplier)}x"
        ]
        if group.peak_rate_enabled and group.peak_rate_multiplier is not None:
            details.append(f"高峰倍率：{format_multiplier(group.peak_rate_multiplier)}x")
        if group.dynamic_rate_enabled and group.dynamic_rate_markup is not None:
            details.append(f"动态加成：{format_multiplier(group.dynamic_rate_markup)}x")
        lines.append(" ".join(details))

    if report.minimum_multiplier is not None:
        lines.extend(
            [
                "",
                f"最低基础倍率：{format_multiplier(report.minimum_multiplier)}x",
                "最低倍率分组：" + "、".join(group.name for group in report.minimum_groups),
            ]
        )
    return "\n".join(lines)


def split_message(text: str, max_length: int = 3000) -> list[str]:
    """Split text on line boundaries, hard-splitting only oversized lines."""

    limit = max(1, int(max_length))
    if not text:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            chunks.append("\n".join(current))
            current = []
            current_length = 0

    for line in text.splitlines():
        if len(line) > limit:
            flush()
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            continue

        extra = len(line) if not current else len(line) + 1
        if current and current_length + extra > limit:
            flush()
        current.append(line)
        current_length += len(line) if len(current) == 1 else len(line) + 1
    flush()
    return chunks


def _extract_group_list(payload: Any) -> list[Mapping[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return None

    for key in ("data", "groups", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = _extract_group_list(value)
            if nested is not None:
                return nested
    return None


def _normalize_group(raw: Mapping[str, Any]) -> Sub2APIGroupRate | None:
    status = str(raw.get("status") or "active").strip().lower()
    rate = _to_float(raw.get("rate_multiplier"))
    if rate is None:
        return None

    group_id = str(raw.get("id") or raw.get("group_id") or "")
    name = str(raw.get("name") or raw.get("group_name") or group_id or "未命名分组").strip()
    platform = str(raw.get("platform") or "unknown").strip()
    peak_multiplier = _to_float(raw.get("peak_rate_multiplier"))
    dynamic_markup = _to_float(raw.get("dynamic_rate_markup"))
    return Sub2APIGroupRate(
        group_id=group_id,
        name=name,
        platform=platform,
        status=status,
        rate_multiplier=rate,
        model_names=tuple(_extract_model_names(raw)),
        peak_rate_enabled=_to_bool(raw.get("peak_rate_enabled")),
        peak_rate_multiplier=peak_multiplier,
        dynamic_rate_enabled=_to_bool(raw.get("dynamic_rate_enabled")),
        dynamic_rate_markup=dynamic_markup,
    )


def _extract_model_names(group: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("models", "model_pricing", "models_list_config", "supported_model_scopes", "model_routing"):
        _collect_model_names(group.get(key), names)
    return _unique(names)


def _collect_model_names(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        if value.strip():
            output.append(value.strip())
        return
    if isinstance(value, Mapping):
        for key in ("model", "model_name", "name", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                output.append(candidate.strip())
                return

        nested_keys = ("models", "model_names", "allowed_models", "items", "data")
        nested_values_found = False
        for key in nested_keys:
            if key in value:
                nested_values_found = True
                _collect_model_names(value[key], output)
        if nested_values_found:
            return

        metadata_keys = {
            "enabled",
            "default",
            "platform",
            "description",
            "input_price",
            "output_price",
            "cache_read_price",
            "cache_write_price",
        }
        for key, nested in value.items():
            if isinstance(key, str) and key.strip() and key not in metadata_keys:
                output.append(key.strip())
            if isinstance(nested, (list, tuple, Mapping)):
                _collect_model_names(nested, output)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _collect_model_names(item, output)


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
