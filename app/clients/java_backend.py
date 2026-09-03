import asyncio
import logging
from datetime import date

import httpx
from fastapi import HTTPException

from app.config import Settings

logger = logging.getLogger("orange-diary-ai")


class JavaBackendClient:
    """通过 Java 的 JWT 鉴权接口读取当前用户真实数据。"""
    def __init__(self, settings: Settings):
        self.settings = settings

    async def load_current_user_context(self, authorization: str | None) -> dict | None:
        if not authorization:
            return None
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authorization 请求头格式应为 Bearer Token")
        headers, today = {"Authorization": authorization}, date.today().isoformat()
        try:
            async with httpx.AsyncClient(timeout=self.settings.java_backend_timeout_seconds) as client:
                # 两个查询相互独立，并发执行可减少聊天接口等待时间。
                user_response, summary_response = await asyncio.gather(
                    client.get(f"{self.settings.java_backend_base_url}/api/users/me", headers=headers),
                    client.get(f"{self.settings.java_backend_base_url}/api/daily-summaries", headers=headers, params={"date": today}),
                )
            if user_response.status_code == 401 or summary_response.status_code == 401:
                raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
            user_response.raise_for_status()
            summary_response.raise_for_status()
            user_body, summary_body = user_response.json(), summary_response.json()
            if user_body.get("code") != 0 or summary_body.get("code") != 0:
                raise ValueError("Java 业务服务返回失败")
            return self._build_context(today, user_body.get("data") or {}, summary_body.get("data") or {})
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            logger.exception("Java backend request failed")
            raise HTTPException(status_code=502, detail="无法读取用户健康数据") from exc
        except (KeyError, ValueError, TypeError) as exc:
            logger.exception("Java backend returned an unexpected response")
            raise HTTPException(status_code=502, detail="用户健康数据格式异常") from exc

    @staticmethod
    def _build_context(today: str, user: dict, summary: dict) -> dict:
        profile, goal = user.get("profile") or {}, user.get("activeGoal") or {}
        # 只保留模型生成饮食建议需要的数据，避免泄露手机号、JWT、数据库 ID。
        return {
            "date": today,
            "nickname": user.get("nickname"),
            "profile": {"gender": profile.get("gender"), "heightCm": profile.get("heightCm"), "currentWeightKg": profile.get("currentWeightKg"), "targetWeightKg": profile.get("targetWeightKg"), "activityLevel": profile.get("activityLevel")},
            "goal": {"goalType": goal.get("goalType"), "dailyIntakeTarget": goal.get("dailyIntakeTarget"), "dailyBurnTarget": goal.get("dailyBurnTarget")},
            "todaySummary": {"totalIntakeCalorie": summary.get("totalIntakeCalorie"), "totalBurnCalorie": summary.get("totalBurnCalorie"), "availableIntakeCalorie": summary.get("availableIntakeCalorie"), "dietCount": summary.get("dietCount"), "exerciseCount": summary.get("exerciseCount"), "weightKg": summary.get("weightKg")},
        }
