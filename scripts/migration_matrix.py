"""生成 M0 迁移矩阵：Java 端点 → Python 实现状态，以及表清单。

M0 的交付物是"可追溯基线"，不是一份手写清单——手写的清单第二次改代码就过期，
而这份脚本可以随时重跑并和 git diff 对比。

用法：
    python scripts/migration_matrix.py
    python scripts/migration_matrix.py --java-root ../orangeDiary --output docs/MIGRATION_MATRIX.md
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 以 `python scripts/migration_matrix.py` 运行时 sys.path[0] 是 scripts/，
# 直接 import app 会失败；显式把项目根加进去。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CLASS_MAPPING = re.compile(r'@RequestMapping\("([^"]*)"\)')
_METHOD_MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch)Mapping(?:\((?:value\s*=\s*)?\"([^\"]*)\")?")
_PUBLIC_METHOD = re.compile(r"public\s+[\w<>,\[\]\. ]+\s+(\w+)\s*\(")
_CREATE_TABLE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?`?(\w+)`?", re.IGNORECASE)
_ENTITY_TABLE = re.compile(r'@TableName\("(\w+)"\)')

# prefix → (Python 模块, 状态)。状态只写"已迁移/未开始"，不做百分比估算：
# 一个端点能返回 200 但事务和权限没对齐，算完成是自欺。
_MIGRATED_PREFIXES = {
    "/api/auth": ("app/api/auth.py", "已迁移（M1）"),
    "/api/users": ("app/api/users.py", "已迁移（M2）"),
    "/api/foods": ("app/api/foods.py", "已迁移（M2）"),
    "/api/diet-records": ("app/api/diet.py", "已迁移（M2）"),
    "/api/exercises": ("app/api/exercise.py", "已迁移（M2）"),
    "/api/exercise-records": ("app/api/exercise.py", "已迁移（M2）"),
    "/api/weight-records": ("app/api/weight.py", "已迁移（M2）"),
    "/api/daily-summaries": ("app/api/summary.py", "已迁移（M2）"),
    "/api/check-ins": ("app/api/checkin.py", "已迁移（M2）"),
    "/api/notifications": ("app/api/notifications.py", "已迁移（M2）"),
    "/api/posts": ("app/api/posts.py", "已迁移（M3）"),
    "/api/files/images": ("app/api/images.py", "已迁移（M3）"),
    "/api/avatars": ("app/api/avatars.py", "已迁移（M3）"),
    "/api/admin/posts": ("app/api/posts.py", "已迁移（M3）"),
    "/api/admin/community-reports": ("app/api/community_reports.py", "已迁移（M3）"),
    "/api/admin/action-logs": ("app/api/admin_logs.py", "已迁移（M3）"),
    "/api/admin/ai": ("app/api/admin_ai.py", "已迁移（M3）"),
    "/api/reports/diet": ("app/api/reports.py", "已迁移（M3）"),
    "/api/ai": ("app/api/ai.py", "已迁移（M4）"),
    "/health": ("app/main.py", "已迁移（M0）"),
}


def modeled_tables() -> set[str]:
    """已建模的表直接读 ORM 元数据，避免脚本里再维护一份手写清单。"""
    try:
        from app.db.base import Base
        import app.models  # noqa: F401  触发模型注册
    except Exception as exc:  # pragma: no cover - 缺依赖时仍想生成矩阵
        print(f"警告：无法读取 ORM 元数据（{exc}），表建模状态将全部标为否")
        return set()
    return set(Base.metadata.tables)


@dataclass
class Endpoint:
    http_method: str
    path: str
    handler: str
    controller: str


def _join(prefix: str, path: str) -> str:
    combined = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if path else prefix
    return combined or "/"


def parse_controllers(controller_dir: Path) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for file in sorted(controller_dir.glob("*Controller.java")):
        source = file.read_text(encoding="utf-8")
        class_match = _CLASS_MAPPING.search(source)
        prefix = class_match.group(1) if class_match else ""
        for match in _METHOD_MAPPING.finditer(source):
            http_method = match.group(1).upper()
            path = match.group(2) or ""
            tail = source[match.end() : match.end() + 400]
            handler_match = _PUBLIC_METHOD.search(tail)
            handler = handler_match.group(1) if handler_match else "?"
            endpoints.append(
                Endpoint(http_method, _join(prefix, path), handler, file.stem)
            )
    return endpoints


def parse_tables(java_root: Path) -> tuple[list[str], list[str]]:
    sql_tables: set[str] = set()
    for sql_file in (java_root / "src/main/resources").glob("*.sql"):
        for match in _CREATE_TABLE.finditer(sql_file.read_text(encoding="utf-8")):
            sql_tables.add(match.group(1))
    entity_tables: set[str] = set()
    for entity in (java_root / "src/main/java").rglob("entity/*.java"):
        match = _ENTITY_TABLE.search(entity.read_text(encoding="utf-8"))
        if match:
            entity_tables.add(match.group(1))
    return sorted(sql_tables), sorted(entity_tables)


def status_for(path: str) -> tuple[str, str]:
    for prefix, (module, status) in _MIGRATED_PREFIXES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return status, module
    return "未开始", "—"


def render(endpoints: list[Endpoint], sql_tables: list[str], entity_tables: list[str]) -> str:
    migrated = sum(1 for e in endpoints if status_for(e.path)[0] != "未开始")
    by_controller: dict[str, list[Endpoint]] = {}
    for endpoint in endpoints:
        by_controller.setdefault(endpoint.controller, []).append(endpoint)

    lines = [
        "# 迁移矩阵（M0 基线）",
        "",
        "> 本文件由 `scripts/migration_matrix.py` 生成，不要手改。",
        "> 重新生成：`python scripts/migration_matrix.py`",
        "",
        f"- Java 端点总数：**{len(endpoints)}**",
        f"- 已迁到 Python：**{migrated}**（{migrated / len(endpoints) * 100:.1f}%）",
        f"- SQL 中出现的表：**{len(sql_tables)}**，entity 标注的表：**{len(entity_tables)}**",
        "",
        "## 端点",
        "",
        "| 方法 | 路径 | Java 处理函数 | Python 状态 | 位置 |",
        "|---|---|---|---|---|",
    ]
    for controller in sorted(by_controller):
        for endpoint in by_controller[controller]:
            status, module = status_for(endpoint.path)
            lines.append(
                f"| {endpoint.http_method} | `{endpoint.path}` | {endpoint.handler} | {status} | `{module}` |"
            )

    lines += [
        "",
        "## 表",
        "",
        "| 表名 | 出现在 SQL | 有 entity | M1 是否已建模 |",
        "|---|---|---|---|",
    ]
    modeled = modeled_tables()
    for table in sorted(set(sql_tables) | set(entity_tables)):
        lines.append(
            f"| `{table}` | {'是' if table in sql_tables else '—'} | "
            f"{'是' if table in entity_tables else '—'} | {'是' if table in modeled else '否'} |"
        )

    lines += [
        "",
        "## 说明",
        "",
        "- 「已迁移」只代表接口在 Python 侧存在且单测覆盖，**不代表**与 Java 的差分测试通过。",
        "- M2 的完成定义是「Java 停机仍可完成旧业务流程」，届时这张表里不应再有「未开始」。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Java → Python 迁移矩阵")
    parser.add_argument("--java-root", default=str(PROJECT_ROOT.parent / "orangeDiary"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "docs" / "MIGRATION_MATRIX.md"))
    args = parser.parse_args()

    java_root = Path(args.java_root)
    controller_dir = java_root / "src/main/java/com/example/orangediary/controller"
    if not controller_dir.is_dir():
        raise SystemExit(f"找不到 controller 目录：{controller_dir}")

    endpoints = parse_controllers(controller_dir)
    sql_tables, entity_tables = parse_tables(java_root)
    content = render(endpoints, sql_tables, entity_tables)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"已生成 {output}：{len(endpoints)} 个端点，{len(sql_tables)} 张表")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
