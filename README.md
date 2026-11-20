# OrangeDiary · AI 饮食助手

基于 Python FastAPI 与 Vue 3 的饮食管理及 AI 助手项目。前后端源码集中在本仓库，前端通过同源 `/api` 调用 Python 后端。

## 仓库结构

```text
orangeDiary-ai/
├── backend/       # Python 后端、数据库迁移、知识库、测试与部署配置
├── frontend/      # Vue 前端、页面、组件及前端测试
├── .github/       # 前后端 CI
└── README.md
```

## 项目功能

- 饮食、运动、体重与健康目标管理，以及社区、通知和健康报告。
- AI 饮食问答、流式回复、意图识别、业务数据查询和记录确认。
- 营养与烹饪知识检索，结合向量、BM25、RRF 和可配置的 Rerank，返回参考来源。
- 菜谱、食材库存、餐单、购物清单与饮食复盘相关功能。

具体实现与验收记录见 `backend/docs/`；功能效果需结合实际服务配置与联调结果确认。

## 本地启动（PowerShell）

### Python 后端

新克隆仓库先创建环境、安装依赖并配置本地环境变量：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# 根据 backend/README.md 配置 .env、数据库迁移和外部依赖
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

本次从旧结构迁移的本机已保留根目录 `.venv`，不必重新创建环境。在 `backend` 目录使用：

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

本地原有配置已放在 `backend/.env`，被 Git 忽略。PyCharm 的工作目录应设为 `backend`，模块为 `uvicorn`，参数同上。数据库、Redis、MinIO、Ollama 和 Qdrant 的配置与迁移说明见 [后端说明](backend/README.md)。

- 健康检查：<http://127.0.0.1:8000/health>
- 就绪检查：<http://127.0.0.1:8000/ready>
- 接口文档：<http://127.0.0.1:8000/docs>

### Vue 前端

另开一个终端，从仓库根目录执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问 <http://127.0.0.1:5173>。Vite 默认将 `/api` 代理到 `http://127.0.0.1:8000`。前端源码说明见 [前端说明](frontend/README.md)。

## 测试与构建

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -m "not integration and not live" -q
cd ../frontend
node --test tests/*.test.mjs
npm run build
```

新建在 `backend/.venv` 的环境使用 `.\.venv\Scripts\python.exe`。集成测试使用隔离测试库，详情见 `backend/deploy/README.md`。真实模型调用需要单独配置，离线测试通过不代表真实模型联调通过。

## Docker 与 CI

从仓库根目录构建后端镜像：

```powershell
docker build -t orange-diary-backend ./backend
```

前端 `npm run build` 输出到 `frontend/dist`，部署时由 Web 服务提供静态文件并代理 `/api` 到后端。GitHub Actions 分别在 `backend`、`frontend` 工作目录执行检查。

本地 `.env`、虚拟环境、模型权重、知识索引、`node_modules` 和 `dist` 不提交到仓库；知识索引及模型准备方式见后端说明。
