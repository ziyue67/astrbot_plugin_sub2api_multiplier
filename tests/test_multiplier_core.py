from __future__ import annotations

import httpx
import pytest

from multiplier_core import (
    Sub2APIGroupRate,
    Sub2APIInstanceConfig,
    build_groups_url,
    build_report,
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
