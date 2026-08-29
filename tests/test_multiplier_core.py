from __future__ import annotations

import httpx
import pytest

from multiplier_core import (
    Sub2APIGroupRate,
    Sub2APIGroupHealth,
    Sub2APIInstanceConfig,
    build_groups_url,
    build_monitor_url,
    build_report,
    fetch_group_health,
    fetch_groups,
    format_report,
    split_message,
)


def test_build_groups_url_validates_domain_and_query():
    assert build_groups_url("https://example.com/", False) == (
        "https://example.com/api/v1/admin/groups/all?include_inactive=false"
    )
    assert build_groups_url("https://example.com", True).endswith("include_inactive=true")

    with pytest.raises(Exception, match="域名无效"):
        build_groups_url("example.com")


def test_build_monitor_url_uses_platform_group_and_validates_range():
    assert build_monitor_url("https://example.com/", "24h") == (
        "https://example.com/api/v1/admin/channel-monitor-v2/matrix?"
        "range=24h&group_by=platform_group"
    )

    with pytest.raises(Exception, match="时间范围无效"):
        build_monitor_url("https://example.com", "2h")


@pytest.mark.asyncio
async def test_fetch_groups_sends_admin_api_key_and_parses_models():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "name": "便宜组",
                        "platform": "openai",
                        "status": "active",
                        "rate_multiplier": "0.5",
                        "model_pricing": [
                            {"model": "gpt-4o", "input_price": 1},
                            {"model": "gpt-4o-mini", "input_price": 0.1},
                        ],
                    }
                ]
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        groups = await fetch_groups(
            Sub2APIInstanceConfig("test", "https://example.com", "secret-key"),
            client=client,
        )
    finally:
        await client.aclose()

    assert captured["key"] == "secret-key"
    assert captured["url"].endswith("include_inactive=false")
    assert groups[0].rate_multiplier == 0.5
    assert groups[0].model_names == ("gpt-4o", "gpt-4o-mini")


@pytest.mark.asyncio
async def test_fetch_groups_filters_inactive_groups():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": 1, "name": "启用组", "platform": "openai", "status": "active", "rate_multiplier": 1},
                    {"id": 2, "name": "停用组", "platform": "openai", "status": "inactive", "rate_multiplier": 0.1},
                ]
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        groups = await fetch_groups(
            Sub2APIInstanceConfig("test", "https://example.com", "secret-key"),
            client=client,
        )
    finally:
        await client.aclose()

    assert [group.name for group in groups] == ["启用组"]


@pytest.mark.asyncio
async def test_fetch_group_health_parses_cache_rate_and_status():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {
                            "group_id": 1,
                            "group_name": "便宜组",
                            "platform": "openai",
                            "metrics": {
                                "cache_rate": 0.23,
                                "cache_rate_denominator": 100,
                            },
                            "health": {"overall": "healthy", "cache_score": 0.9},
                        }
                    ]
                }
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        health = await fetch_group_health(
            Sub2APIInstanceConfig("test", "https://example.com", "secret-key"),
            client=client,
        )
    finally:
        await client.aclose()

    assert captured["key"] == "secret-key"
    assert "group_by=platform_group" in captured["url"]
    assert health[0].group_id == "1"
    assert health[0].cache_rate == 0.23
    assert health[0].cache_rate_denominator == 100
    assert health[0].overall == "healthy"


def test_build_report_returns_all_tied_minimum_groups_and_models():
    groups = [
        Sub2APIGroupRate("1", "标准组", "openai", "active", 1.0, ("gpt-4o",)),
        Sub2APIGroupRate("2", "低价组", "openai", "active", 0.5, ("gpt-4o-mini",)),
        Sub2APIGroupRate("3", "另一个低价组", "anthropic", "active", 0.5, ("claude-3-5",)),
    ]

    report = build_report(groups)
    assert report.minimum_multiplier == 0.5
    assert [group.name for group in report.minimum_groups] == ["低价组", "另一个低价组"]
    text = format_report("主站", report)
    assert "最低基础倍率：0.5x" in text
    assert "gpt-4o-mini" not in text
    assert "claude-3-5" not in text
    assert "最低倍率模型" not in text


def test_format_report_joins_health_by_group_id_and_hides_cache_when_sample_is_low():
    groups = [
        Sub2APIGroupRate("1", "健康组", "openai", "active", 1.0),
        Sub2APIGroupRate("2", "样本不足组", "openai", "active", 2.0),
    ]
    health = [
        Sub2APIGroupHealth("1", "健康组", "openai", "healthy", 0.23, 0.9, 100),
        Sub2APIGroupHealth("2", "样本不足组", "openai", "warning", 0.9, 0.2, 4, minimum_sample=5),
    ]

    text = format_report(
        "主站",
        build_report(groups),
        health=health,
        monitor_range="24h",
    )
    assert "健康组 | openai | 基础倍率：1x 渠道：健康 缓存率：23.0%" in text
    assert "样本不足组 | openai | 基础倍率：2x 渠道：警告 缓存率：无数据" in text
    assert "模型：" not in text


def test_format_report_keeps_multiplier_result_when_monitor_is_unavailable():
    group = Sub2APIGroupRate("1", "便宜组", "openai", "active", 0.025)
    text = format_report(
        "主站",
        build_report([group]),
        monitor_error="V2不可用",
    )
    assert "基础倍率：0.025x" in text
    assert "渠道状态：V2不可用" in text
    assert "https://" not in text
    assert "secret-key" not in text


def test_report_shows_peak_and_dynamic_multipliers_separately():
    group = Sub2APIGroupRate(
        "1",
        "高峰组",
        "openai",
        "active",
        1.0,
        peak_rate_enabled=True,
        peak_rate_multiplier=1.5,
        dynamic_rate_enabled=True,
        dynamic_rate_markup=1.2,
    )
    text = format_report("主站", build_report([group]))
    assert "高峰倍率：1.5x" in text
    assert "动态加成：1.2x" in text


def test_split_message_prefers_line_boundaries_and_enforces_limit():
    chunks = split_message("第一行\n第二行\n第三行", max_length=5)
    assert chunks == ["第一行", "第二行", "第三行"]
    assert all(len(chunk) <= 5 for chunk in chunks)

    long_chunks = split_message("abcdefghij", max_length=3)
    assert long_chunks == ["abc", "def", "ghi", "j"]
