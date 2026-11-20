"""价格 Provider 接口。

设计 §8.2 要求：无可验证报价时显示"暂无可靠参考价"，不能从搜索摘要虚构价格。
所以这里把"取价"做成可插拔接口：

- 默认 `UnavailablePriceProvider`：不返回任何报价，调用方据此标 `priceStatus=unknown`、
  `budgetStatus=unverified`，而不是编一个数；
- 接入真实供应商时实现 `quote()` 并注入，不改变购物服务逻辑。

`PRICE_PROVIDER=none`（默认）时始终保持不可用，模拟数据只允许出现在测试里。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class PriceQuote:
    unit_price_low: Decimal
    unit_price_high: Decimal
    currency: str = "CNY"
    region: str = ""
    channel: str | None = None
    price_type: str = "regular"
    source_url: str | None = None
    evidence_text: str | None = None
    conditions: str | None = None


class PriceProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def quote(self, label: str, spec: str | None, region: str | None) -> PriceQuote | None: ...


class UnavailablePriceProvider:
    name = "none"

    @property
    def available(self) -> bool:
        return False

    async def quote(self, label: str, spec: str | None, region: str | None) -> PriceQuote | None:
        return None


def build_price_provider() -> PriceProvider:
    """按环境变量选择供应商；未实现的一律回落到不可用，不返回假报价。"""
    provider = os.getenv("PRICE_PROVIDER", "none").strip().lower()
    if provider in {"", "none", "unavailable"}:
        return UnavailablePriceProvider()
    # 已声明供应商但尚未接入实现：保持不可用，明确告知而不是伪造。
    return UnavailablePriceProvider()
