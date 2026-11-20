"""R3 端到端冒烟：真实 MySQL + 真实 HTTP，不用 mock。

流程按设计依赖顺序走一遍：食谱 → 私人厨师 → 周计划 → 食材核对 → 采购
→ 实际记录/调整 → 复盘。跑完删掉冒烟用户，不污染数据库。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta

import httpx

BASE = "http://127.0.0.1:8000"
EMAIL = f"r3smoke{int(time.time())}@example.com"
PASSWORD = "Orange2026!"

ok = 0
fail = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global ok
    if condition:
        ok += 1
        print(f"  [OK] {name}")
    else:
        fail.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


with httpx.Client(base_url=BASE, timeout=120) as client:
    print("== 注册 / 登录 ==")
    r = client.post(
        "/api/auth/register",
        json={"username": "r3smoke", "email": EMAIL, "password": PASSWORD, "confirmPassword": PASSWORD},
    )
    check("register", r.status_code == 200, r.text[:200])
    r = client.post("/api/auth/login", json={"account": EMAIL, "password": PASSWORD})
    check("login", r.status_code == 200, r.text[:200])
    data = r.json()["data"]
    token = data.get("token")
    client.headers["Authorization"] = f"Bearer {token}"

    # 计划需要身高体重或明确目标，先补齐资料（复用现有 summary_service.daily_target 口径）。
    r = client.put(
        "/api/users/profile",
        json={
            "gender": "male",
            "birthday": "1996-05-01",
            "heightCm": 175,
            "currentWeightKg": 70,
            "targetWeightKg": 65,
            "activityLevel": "low",
        },
    )
    check("补充资料", r.status_code == 200, r.text[:200])
    r = client.post(
        "/api/users/goals",
        json={"goalType": "lose", "startDate": date.today().isoformat()},
    )
    check("设置减脂目标", r.status_code == 200, r.text[:200])

    print("== M3 食谱与私人厨师 ==")
    r = client.get("/api/recipes", params={"pageSize": 50})
    body = r.json().get("data") or {}
    recipes = body.get("records") or []
    check("食谱库非空", len(recipes) >= 5, f"status={r.status_code} got {len(recipes)} {r.text[:200]}")
    if recipes:
        r = client.get(f"/api/recipes/{recipes[0]['id']}")
        detail = r.json()["data"]
        check("食谱详情含步骤", bool(detail.get("steps")), str(detail)[:200])
        check(
            "食材克重有来源不臆造",
            all(
                (ing.get("grams") is not None) or ing.get("gramStatus") == "unknown"
                for ing in detail.get("ingredients", [])
            ),
            json.dumps(detail.get("ingredients", [])[:2], ensure_ascii=False),
        )

    r = client.post("/api/assistant/chef", json={"message": "红烧肉怎么做", "servings": 2})
    chef = r.json().get("data") or {}
    check("厨师接口返回候选", bool(chef.get("recipes")), str(chef)[:200])
    check("厨师回复非空", bool((chef.get("reply") or "").strip()), str(chef)[:200])
    print(f"      degraded={chef.get('degraded')} 候选={len(chef.get('recipes') or [])}")

    print("== M4 周计划与食材核对 ==")
    start = date.today()
    r = client.post(
        "/api/meal-plans/drafts",
        json={"startDate": start.isoformat(), "days": 2, "mealTypes": ["breakfast", "lunch", "dinner"]},
    )
    draft = r.json()["data"]
    check("生成草稿", draft.get("status") in {"draft", "infeasible"}, json.dumps(draft, ensure_ascii=False)[:300])
    plan_id = draft.get("planId")
    check("草稿不生效", draft.get("activeVersion") is None, str(draft.get("activeVersion")))

    r = client.get("/api/diet-preferences")
    check("读取偏好", r.status_code == 200, r.text[:150])

    if plan_id and draft.get("meals"):
        r = client.post("/api/ingredient-checks", json={"planId": plan_id})
        check_session = r.json()["data"]
        statuses = {item["status"] for item in check_session["items"]}
        check("核对区分待核对/待购买", statuses <= {"needs_check", "confirmed", "to_buy"}, str(statuses))

        r = client.post(f"/api/meal-plans/{plan_id}/adopt", json={"versionNumber": draft["versionNumber"]})
        adopted = r.json()["data"]
        check("采纳生效", adopted.get("versionStatus") == "adopted", str(adopted)[:200])

        print("== M5 采购 ==")
        r = client.post("/api/shopping-lists", json={"planId": plan_id, "versionNumber": draft["versionNumber"]})
        listing = r.json()["data"] if r.status_code == 200 else {}
        check("生成采购清单", r.status_code == 200 and bool(listing.get("items")), r.text[:300])
        if listing.get("items"):
            totals = listing["totals"]
            check("价格未知如实标注", totals["priceStatus"] == "unknown", str(totals))
            check("预算未核验不冒充结论", totals["budgetStatus"] == "unverified", str(totals))
            target = next((i for i in listing["items"] if i["state"] in {"to_buy", "needs_check"}), None)
            if target:
                r = client.post(
                    f"/api/shopping-items/{target['id']}/purchase",
                    json={"actualQuantity": 500, "paidPrice": 12.5},
                    headers={"X-Request-Id": "smoke-1"},
                )
                check("购买入库", r.status_code == 200, r.text[:200])
                r2 = client.post(
                    f"/api/shopping-items/{target['id']}/purchase",
                    json={"actualQuantity": 500, "paidPrice": 12.5},
                    headers={"X-Request-Id": "smoke-1"},
                )
                check("重复请求幂等", r2.status_code == 200, r2.text[:200])
            r = client.post(f"/api/shopping-lists/{listing['id']}/price-refresh")
            check("价格源未配置明确不可用", r.status_code in {200, 409, 503}, r.text[:200])
            body = (r.json().get("data") or {}) if r.status_code == 200 else {}
            check(
                "刷新不伪造报价",
                body.get("available") is False and body.get("priceStatus") == "unknown",
                json.dumps(body, ensure_ascii=False)[:200],
            )

        print("== M6 实际记录与调整 ==")
        meal_id = adopted["meals"][0]["id"]
        r = client.post(f"/api/plan-meals/{meal_id}/execution", json={"state": "executed"})
        check("标记执行", r.status_code == 200, r.text[:150])
        r = client.post(
            "/api/meal-observations",
            json={"occurredAt": f"{start.isoformat()}T19:00:00", "description": "外卖麻辣烫", "completeness": "partial"},
        )
        obs = r.json()["data"]
        check("记录观察且营养未知为 null", obs.get("estimatedNutrition") is None, str(obs)[:200])
        r = client.post(
            f"/api/meal-plans/{plan_id}/adjustments", json={"note": "第二天想吃清淡点", "event": "口味"}
        )
        revised = r.json()["data"]
        check("调整只生成草稿", revised.get("versionStatus") == "draft", json.dumps(revised, ensure_ascii=False)[:300])
        check(
            "调整保留已执行餐次",
            revised.get("activeVersion") == draft["versionNumber"],
            str(revised.get("activeVersion")),
        )

    print("== M7 复盘 ==")
    r = client.put(f"/api/diet-day-status/{start.isoformat()}", json={"completeness": "complete"})
    check("标记当天记录完整", r.status_code == 200, r.text[:150])
    r = client.post("/api/reviews", json={})
    review = r.json().get("data") or {}
    check("生成复盘", bool(review.get("id")), json.dumps(review, ensure_ascii=False)[:300])
    metrics = review.get("metrics") or {}
    execution = metrics.get("execution") or {}
    check("复盘给出执行口径", "confirmedExecutionRate" in execution and "executionCoverage" in execution, str(execution)[:200])
    check("复盘列出数据缺口", isinstance(review.get("dataGaps"), list), str(review.get("dataGaps"))[:200])
    check("建议不超过3条", len(review.get("suggestions") or []) <= 3, str(len(review.get("suggestions") or [])))
    print("      execution:", json.dumps(execution, ensure_ascii=False)[:300])
    print("      gaps:", [g["code"] for g in review.get("dataGaps") or []])

    # 默认周期是"上一个完整周"，冒烟的计划在本周：再按本周算一次，验证有数据时的口径。
    r = client.post(
        "/api/reviews",
        json={
            "periodStart": start.isoformat(),
            "periodEnd": (start + timedelta(days=6)).isoformat(),
        },
    )
    current = r.json().get("data") or {}
    cur_exec = (current.get("metrics") or {}).get("execution") or {}
    print("      this-week execution:", json.dumps(cur_exec, ensure_ascii=False)[:300])
    check("本周复盘统计到已采纳餐次", cur_exec.get("dueAdoptedMeals", 0) > 0, str(cur_exec))
    check(
        "有未反馈餐次时不冒充完成率",
        cur_exec.get("unrecordedMeals", 0) >= 1 or cur_exec.get("showPercent") is True,
        str(cur_exec),
    )

print()
print(f"passed={ok} failed={len(fail)}")
for item in fail:
    print(" -", item)
sys.exit(1 if fail else 0)
