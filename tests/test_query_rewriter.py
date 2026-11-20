"""指代改写单元测试：只钉触发判定与降级方向，不测改写质量（那是模型的事）。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.config import Settings
from app.services import query_rewriter
from app.services.model_gateway import ModelFailure


def _settings(**overrides) -> Settings:
    values = {"deepseek_api_key": "test-key"}
    values.update(overrides)
    return replace(Settings.from_env(), **values)


class FakeGateway:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def execute(self, body, trace_id):
        self.calls.append(body)
        if self.exc is not None:
            raise self.exc
        return self.result


def _gateway(monkeypatch, fake):
    monkeypatch.setattr(query_rewriter, "ModelGateway", lambda *a, **k: fake)
    return fake


def test_needs_rewrite():
    assert query_rewriter.needs_rewrite("那一天最多吃多少")
    assert query_rewriter.needs_rewrite("那这个呢")
    assert query_rewriter.needs_rewrite("它和米饭哪个热量高")
    assert not query_rewriter.needs_rewrite("成年人每天盐应该吃多少")
    # "还/也"不是触发词，避免"减脂还能吃什么"这种无需改写的句子白跑一次改写模型。
    assert not query_rewriter.needs_rewrite("减脂还能吃什么")


@pytest.mark.asyncio
async def test_skip_without_anaphora(monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway())
    result = await query_rewriter.rewrite_query(_settings(), "成年人每天盐吃多少", ["用户 昨天吃咸了"])
    assert result is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_skip_without_history(monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway())
    result = await query_rewriter.rewrite_query(_settings(), "那一天最多吃多少", [])
    assert result is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_skip_without_api_key(monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway())
    result = await query_rewriter.rewrite_query(
        _settings(deepseek_api_key=""), "那一天最多吃多少", ["用户 11月18日早餐吃燕麦"]
    )
    assert result is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_resolves_anaphora(monkeypatch):
    fake = _gateway(
        monkeypatch,
        FakeGateway(result={"choices": [{"message": {"content": '{"query": "11月18日最多吃多少"}'}}]}),
    )
    result = await query_rewriter.rewrite_query(
        _settings(), "那一天最多吃多少", ["用户 我11月18日早餐吃了燕麦"]
    )
    assert result == "11月18日最多吃多少"
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "query-rewrite"


@pytest.mark.asyncio
async def test_same_text_returns_none(monkeypatch):
    _gateway(
        monkeypatch,
        FakeGateway(result={"choices": [{"message": {"content": '{"query": "那一天最多吃多少"}'}}]}),
    )
    result = await query_rewriter.rewrite_query(_settings(), "那一天最多吃多少", ["用户 早餐吃燕麦"])
    assert result is None


@pytest.mark.asyncio
async def test_plain_text_output_is_accepted(monkeypatch):
    _gateway(
        monkeypatch,
        FakeGateway(result={"choices": [{"message": {"content": "11月18日最多吃多少"}}]}),
    )
    result = await query_rewriter.rewrite_query(
        _settings(), "那一天最多吃多少", ["用户 我11月18日早餐吃了燕麦"]
    )
    assert result == "11月18日最多吃多少"


@pytest.mark.asyncio
async def test_failure_returns_none(monkeypatch):
    _gateway(monkeypatch, FakeGateway(exc=ModelFailure("upstream_timeout", 504)))
    result = await query_rewriter.rewrite_query(_settings(), "那一天最多吃多少", ["用户 早餐吃燕麦"])
    assert result is None
