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

    async def validate_token(self, authorization: str | None) -> int:
        """让 Java 成为唯一的 JWT、Redis 会话和账号状态校验方，并返回可信用户 ID。"""
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="请先登录")
        body = await self._get_json("/api/users/me", authorization)
        user_id = (body.get("data") or {}).get("id")
        if not isinstance(user_id, int) or user_id <= 0:
            raise HTTPException(status_code=502, detail="无法识别当前登录用户")
        return user_id

    async def get_today_summary(self, authorization: str) -> dict:
        """查询今日热量汇总。"""
        today = date.today().isoformat()
        body = await self._get_json("/api/daily-summaries", authorization, {"date": today})
        return {"date": today, "summary": body.get("data") or {}}

    async def get_today_diet_detail(self, authorization: str) -> dict:
        """查询今日饮食明细。"""
        today = date.today().isoformat()
        body = await self._get_json("/api/diet-records", authorization, {"date": today})
        return {"date": today, "records": body.get("data") or []}

    async def get_weight_progress(self, authorization: str) -> dict:
        """查询当前体重、目标体重和累计减重。"""
        body = await self._get_json("/api/weight-records/progress", authorization)
        return {"progress": body.get("data") or {}}

    async def get_user_goal(self, authorization: str) -> dict:
        """查询用户资料和当前目标，只保留目标相关字段。"""
        body = await self._get_json("/api/users/me", authorization)
        user = body.get("data") or {}
        profile, goal = user.get("profile") or {}, user.get("activeGoal") or {}
        return {
            "goal": {
                "goalType": goal.get("goalType"),
                "dailyIntakeTarget": goal.get("dailyIntakeTarget"),
                "dailyBurnTarget": goal.get("dailyBurnTarget"),
                "startDate": goal.get("startDate"),
                "endDate": goal.get("endDate"),
            },
            "profile": {
                "currentWeightKg": profile.get("currentWeightKg"),
                "targetWeightKg": profile.get("targetWeightKg"),
            },
        }

    async def search_food_nutrition(
            self,
            authorization: str,
            food_name: str,
            amount_g: float | None = None,
            nutrition_metric: str | None = None,
    ) -> dict:
        """按用户提供的明确名称查食物，最多返回十个候选，避免向模型传整张食物表。"""
        body = await self._get_json("/api/foods", authorization, {"keyword": food_name, "limit": 10})
        return {
            "foodName": food_name,
            "amountG": amount_g,
            "nutritionMetric": nutrition_metric,
            "candidates": body.get("data") or [],
        }

    async def match_food(self, authorization: str, food_name: str) -> dict:
        """由 Java 用别名、精确名和模糊候选确认模型识别到的食物。"""
        body = await self._get_json("/api/foods/match", authorization, {"name": food_name})
        return body.get("data") or {"matchType": "NOT_FOUND", "candidates": []}

    async def search_exercise_calorie(
            self,
            authorization: str,
            exercise_name: str,
            duration_min: int,
    ) -> dict:
        """查询运动候选和当前体重，为现有 Java 公式提供所需的真实输入。"""
        # 运动候选与当前体重互不依赖，允许并行读取以缩短响应时间。
        body, user_body = await asyncio.gather(
            self._get_json("/api/exercises", authorization, {"keyword": exercise_name, "limit": 10}),
            self._get_json("/api/users/me", authorization),
        )
        user = user_body.get("data") or {}
        profile = user.get("profile") or {}
        return {
            "exerciseName": exercise_name,
            "durationMin": duration_min,
            "currentWeightKg": profile.get("currentWeightKg"),
            "candidates": body.get("data") or [],
        }

    async def _get_json(self, path: str, authorization: str, params: dict | None = None) -> dict:
        """统一处理 Java 查询接口的鉴权、HTTP 错误和业务错误。"""
        headers = {"Authorization": authorization}
        try:
            # Java 服务运行在本机，不能被 VPN/系统 HTTP_PROXY 转发，否则会误报 502。
            async with httpx.AsyncClient(
                    timeout=self.settings.java_backend_timeout_seconds,
                    trust_env=False,
            ) as client:
                response = await client.get(
                    f"{self.settings.java_backend_base_url}{path}",
                    headers=headers,
                    params=params,
                )
            if response.status_code == 401:
                # Java 是会话的唯一裁决方；Python 不自行解释或刷新登录状态。
                raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                # HTTP 成功不代表 Java 业务成功，仍须检查统一业务码。
                raise ValueError("Java 业务服务返回失败")
            return body
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            logger.exception("Java backend request failed: %s", path)
            raise HTTPException(status_code=502, detail="无法读取用户健康数据") from exc
        except (KeyError, ValueError, TypeError) as exc:
            logger.exception("Java backend returned an unexpected response: %s", path)
            raise HTTPException(status_code=502, detail="用户健康数据格式异常") from exc
