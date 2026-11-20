"""今日饮食报告测试。

报告的价值全在"数字别骗人"，所以这里钉的是口径：

- 热量缺失 → 显示"未知"，剩余/进度变 None（不是当成 0 算出一份漂亮的百分比）；
- 营养缺失 → 按 0 汇总，但页面要出现"仅基于已知值"的说明；
- 剩余不加回运动消耗；
- 建议清洗掉 HTML/链接/Markdown，超长按 600 字截断。

PDF 用 pypdf 抽文本断言，不比对二进制——版面微调不该让测试红。
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from pypdf import PdfReader

from app.config import Settings
from app.reports.diet_report import (
    EMPTY_ADVICE,
    UNAVAILABLE_ADVICE,
    Amount,
    DietLine,
    DietReportData,
    ExerciseLine,
    build_advice,
    clean_advice,
    number,
    render_pdf,
)
from app.reports.snapshot import ReportBusy, ReportGenerationGuard

DAY = date(2026, 11, 18)
MOMENT = datetime(2026, 11, 18, 12, 0, 0)


def diet(
    meal: str,
    name: str,
    quantity: str,
    calorie: str | None,
    *,
    protein: str | None = None,
    fat: str | None = None,
    carb: str | None = None,
) -> DietLine:
    decimal = lambda value: Decimal(value) if value is not None else None  # noqa: E731
    return DietLine(
        meal_type=meal,
        food_name=name,
        quantity_g=Decimal(quantity),
        calorie=decimal(calorie),
        protein=decimal(protein),
        fat=decimal(fat),
        carb=decimal(carb),
        record_time=MOMENT,
    )


def report(diets=(), exercises=(), target="1600.00") -> DietReportData:
    return DietReportData(
        date=DAY,
        generated_at=MOMENT,
        target=Decimal(target) if target is not None else None,
        diets=tuple(diets),
        exercises=tuple(exercises),
    )


# ==================== 数值口径 ====================


def test_number_strips_trailing_zeros():
    assert number(Decimal("116.00")) == "116"
    assert number(Decimal("116.25")) == "116.3"
    assert number(Decimal("100")) == "100"
    assert number(Decimal("0")) == "0"
    assert number(None) == "未知"


def test_amount_label_marks_partial_data():
    assert Amount(Decimal("100"), 1, 0).label() == "100"
    assert Amount(Decimal("100"), 1, 2).label() == "已知 100"
    # 一条都没记到热量：不显示 0，显示"未知"。
    assert Amount(Decimal("0"), 0, 2).label() == "未知"


def test_missing_calorie_blocks_remaining_and_progress():
    data = report([diet("lunch", "米饭", "150", "174.00"), diet("lunch", "汤", "300", None)])
    assert data.intake().value == Decimal("174.00")
    assert data.intake().missing_count == 1
    assert data.remaining() is None
    assert data.progress() is None


def test_complete_intake_computes_remaining_and_progress():
    data = report([diet("lunch", "米饭", "150", "800.00")])
    assert data.remaining() == Decimal("800.00")
    assert data.progress() == Decimal("50.0")


def test_burn_is_not_added_back_to_remaining():
    data = report([diet("lunch", "米饭", "150", "800.00")], [ExerciseLine("慢跑", 30, Decimal("300"), MOMENT)])
    # 剩余永远是 目标 - 摄入，运动消耗只是单独展示。
    assert data.remaining() == Decimal("800.00")


def test_missing_nutrients_count_as_zero():
    data = report([diet("lunch", "米饭", "150", "174.00", protein="3.90")])
    assert data.protein().value == Decimal("3.90")
    assert data.fat().value == Decimal("0")
    assert data.fat().complete is True


def test_no_target_means_no_remaining():
    data = report([diet("lunch", "米饭", "150", "800.00")], target=None)
    assert data.has_target() is False
    assert data.remaining() is None
    assert data.progress() is None


def test_meal_groups_by_type():
    data = report(
        [diet("breakfast", "包子", "100", "200.00"), diet("lunch", "米饭", "150", "174.00")]
    )
    assert [row.food_name for row in data.meal("breakfast")] == ["包子"]
    assert [row.food_name for row in data.meal("dinner")] == []


# ==================== 建议清洗 ====================


def test_clean_strips_markup_links_and_lists():
    cleaned = clean_advice("**你好**\n- 第一点\n# 标题\n见 https://example.com/x 详情\n<script>x</script>")
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "<script>" not in cleaned
    assert cleaned.startswith("你好")
    assert "第一点" in cleaned


def test_clean_falls_back_when_empty():
    assert clean_advice("   ") == UNAVAILABLE_ADVICE
    assert clean_advice("") == UNAVAILABLE_ADVICE


def test_clean_truncates_long_text():
    cleaned = clean_advice("字" * 700)
    assert len(cleaned) == 601
    assert cleaned.endswith("…")


def test_advice_without_api_key_is_unavailable():
    """没配 key 不调模型，直接给固定文案——不能悄悄少打一段字让报告看起来正常。"""
    settings = replace(Settings.from_env(), deepseek_api_key="")
    data = report([diet("lunch", "米饭", "150", "174.00")])
    assert asyncio.run(build_advice(data, settings)) == UNAVAILABLE_ADVICE


def test_empty_report_advice_is_explicit():
    assert asyncio.run(build_advice(report(), Settings.from_env())) == EMPTY_ADVICE


# ==================== 渲染 ====================


def _text(pdf: bytes) -> str:
    return PdfReader(io.BytesIO(pdf)).pages[0].extract_text()


def test_render_contains_key_numbers():
    data = report(
        [
            diet("breakfast", "热干面", "250", "420.00", protein="12.50", fat="14.20", carb="58.30"),
            diet("lunch", "米饭", "150", "174.00", protein="3.90", fat="0.45", carb="38.85"),
        ],
        [ExerciseLine("慢跑", 30, Decimal("220.00"), MOMENT)],
    )
    pdf = render_pdf(data, "今天不错，继续保持。")
    assert pdf[:5] == b"%PDF-"
    text = _text(pdf)
    for expected in ("今日饮食报告", "已摄入", "热干面", "慢跑", "每日建议", "今天不错，继续保持。"):
        assert expected in text


def test_empty_day_is_labelled_not_silent():
    pdf = render_pdf(report(), "暂无数据")
    text = _text(pdf)
    assert "暂无饮食记录（不代表今天未进食）" in text


def test_unknown_meal_type_gets_its_own_section():
    data = report([diet("brunch", "旧数据早餐", "100", "50.00")])
    text = _text(render_pdf(data, "建议"))
    assert "未分类" in text
    assert "旧数据早餐" in text


def test_incomplete_data_is_disclosed():
    data = report([diet("lunch", "米饭", "150", None)])
    text = _text(render_pdf(data, "建议"))
    assert "热量数据不完整，仅展示已知值" in text
    # 热量缺失时汇总槽位必须是"未知"，不能是 0。
    assert "未知" in text


def test_missing_macros_count_as_zero_and_stay_complete():
    """营养缺失按 0 汇总，所以占比永远按"完整"渲染。

    这与 Java 完全一致：`nutrientSum` 先把 null 映射成 0，
    `missingCount` 因此恒为 0，页面上"部分营养数据缺失"那行是**不可达分支**。
    这里锁住现状，避免有人"顺手修正"后两个实现的占比口径分叉。
    """
    data = report([diet("lunch", "米饭", "150", "174.00", protein="3.90")])
    text = _text(render_pdf(data, "建议"))
    assert data.fat().value == Decimal("0")
    assert data.fat().complete is True
    assert "部分营养数据缺失" not in text
    assert "100%" in text


def test_extremely_long_report_stays_within_pdf_limit():
    dishes = [diet("lunch", f"菜品{i}", "100", "50.00") for i in range(400)]
    pdf = render_pdf(report(dishes), "建议")
    page = PdfReader(io.BytesIO(pdf)).pages[0]
    # PDF 单位上限 14400；超长内容靠整体缩放，不拆页、不丢行。
    assert float(page.mediabox.height) <= 14400
    text = _text(pdf)
    assert "菜品0" in text
    assert "菜品399" in text


# ==================== 生成闸门 ====================


def test_guard_blocks_duplicate_generation():
    guard = ReportGenerationGuard()
    guard.acquire(7)
    with pytest.raises(ReportBusy):
        guard.acquire(7)
    guard.release(7)
    guard.acquire(7)  # 释放后可以再进
    guard.release(7)


def test_guard_is_per_user():
    guard = ReportGenerationGuard()
    guard.acquire(1)
    guard.acquire(2)
    guard.release(1)
    guard.release(2)
