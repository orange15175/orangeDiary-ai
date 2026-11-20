"""报告接口（对齐 `DietReportController`）。

两个响应头是必须的：

- `Content-Disposition: attachment; filename*=UTF-8''…`：文件名是中文，
  用 `filename*` 才能避免浏览器把它变成 `%E4%BB%8A…` 或乱码。
- `Cache-Control: private, no-store`：报告里是个人的饮食明细，不能被缓存复用。

生成顺序是"取快照 → 释放事务 → 请求模型 → 画 PDF"。先把事务放掉再去调模型，
否则一次 17 秒的模型等待会一直占着数据库连接。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.reports.diet_report import build_advice, render_pdf
from app.reports.snapshot import ReportBusy, load_today_snapshot, report_guard

router = APIRouter(prefix="/api/reports/diet", tags=["报告"])


@router.get("/today.pdf", summary="下载今日饮食报告")
async def today_report(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    # 先把 id 取出来：`await db.rollback()` 会让 ORM 实例过期，后续再读
    # `user.id` 会触发一次同步懒加载，在 async 上下文里直接 MissingGreenlet。
    user_id = user.id
    try:
        report_guard.acquire(user_id)
    except ReportBusy:
        raise BusinessError(429, "报告正在生成，请稍后重试", 5) from None

    try:
        report = await load_today_snapshot(db, user_id)
        # 快照已经拿到内存里，先结束读事务，别让模型请求和绘图占着连接。
        await db.rollback()
        advice = await build_advice(report, settings)
        pdf = render_pdf(report, advice)
    finally:
        report_guard.release(user_id)

    filename = f"今日饮食报告-{report.date.isoformat()}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"report-{report.date.isoformat()}.pdf\"; "
                f"filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "private, no-store",
        },
    )
