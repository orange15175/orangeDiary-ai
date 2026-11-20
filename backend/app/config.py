import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 检索默认值只在这里定义一次：字段默认值与 from_env 共用，避免两处各写一份后漂移
# （曾经因为字段写 0.60、from_env 写 0.55，导致线上仍按旧阈值判定）。
_DEFAULT_RAG_COLLECTION = "orange_diary_knowledge_v4"
_DEFAULT_RAG_INDEX_VERSION = "knowledge-v4"
_DEFAULT_RAG_TOP_K = "5"
_DEFAULT_RAG_MIN_SCORE = "0.60"
_DEFAULT_RAG_BM25_STRONG_SCORE = "12.0"
_DEFAULT_RAG_MAX_PER_DOCUMENT = "2"

# 检索结果缓存：命中后跳过 Embedding + Qdrant + rerank（约 1.8s）。
# TTL 不能太长——知识库更新后旧答案会继续挂着；300s 是"同一轮对话内复用"的量级。
_DEFAULT_RAG_CACHE_ENABLED = "true"
_DEFAULT_RAG_CACHE_TTL_SECONDS = "300"
_DEFAULT_RAG_CACHE_MAX_ENTRIES = "256"

# 熔断：向量通道连续失败 3 次后 30s 内直接快速失败，不再空等 8s 超时。
# 并发与排队：rerank 是纯 CPU 操作，本机实测并发 4 时单请求从 1.75s 涨到 4.85s，
# 而成功吞吐几乎不变（0.57 → 0.78 rps）——多出来的并发只买到延迟。
# 取 2：P50 2.8s、P95 3.1s，仍在 Java 侧 6s 预算内。机器更强时可上调。
_DEFAULT_RAG_MAX_CONCURRENCY = "2"
_DEFAULT_RAG_MAX_QUEUE = "8"
_DEFAULT_RAG_QUEUE_WAIT_MS = "2000"
# 单用户 60s 内最多 10 次检索，防连点与脚本刷。
_DEFAULT_RAG_USER_LIMIT = "10"
_DEFAULT_RAG_USER_WINDOW_SECONDS = "60"
# 跨进程总量上限；留空表示不启用 Redis 计数（单实例部署足够）。
_DEFAULT_RAG_GLOBAL_LIMIT = "0"
_DEFAULT_RAG_REDIS_URL = ""

_DEFAULT_RAG_BREAKER_FAILURE_THRESHOLD = "3"
_DEFAULT_RAG_BREAKER_RESET_SECONDS = "30"

# Rerank：默认关闭。开启 `local` 需要事先下载模型（scripts/fetch_reranker.py），
# 阈值由 48 题评估集扫出来，不是拍脑袋的最小值。
_DEFAULT_RERANK_PROVIDER = "none"
_DEFAULT_RERANK_MODEL_PATH = str(_PROJECT_ROOT / "knowledge" / "models" / "bge-reranker-base")
_DEFAULT_RERANK_CANDIDATES = "12"
_DEFAULT_RERANK_MIN_SCORE = "0.75"
_DEFAULT_RERANK_TIMEOUT_SECONDS = "3.0"
_DEFAULT_RERANK_MAX_LENGTH = "320"
_DEFAULT_RERANK_BATCH_SIZE = "8"

# 只加载项目根目录的本地 .env；已有进程环境变量优先，不覆盖部署环境注入的值。
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.is_file():
    load_dotenv(dotenv_path=_ENV_FILE, override=False)


@dataclass(frozen=True)
class Settings:
    """集中读取环境变量，业务代码不再散落 os.getenv 调用。"""
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout_seconds: float
    deepseek_input_price_per_million: float
    deepseek_output_price_per_million: float
    # ---- 模型网关可靠性（重试 + 熔断）----
    # 只重试瞬时故障（连接失败 / 超时 / 429 / 5xx）；4xx 与空回答不重试。
    model_retry_max_attempts: int = 3
    model_retry_base_delay_seconds: float = 0.5
    model_retry_backoff_factor: float = 2.0
    model_retry_max_delay_seconds: float = 5.0
    model_breaker_failure_threshold: int = 3
    model_breaker_reset_seconds: float = 30.0
    ai_service_token: str = ""
    # RAG 检索配置；默认值对应本地开发环境的 knowledge-v3 索引（144 片段 / 13 篇文档）。
    # 根目录 knowledge/manifest.json 里残留的 version 字段是建库时的标注，不参与选择，
    # 真正决定读哪份索引的是 rag_index_version（对应 knowledge/index/bm25/<版本>.json）。
    rag_enabled: bool = True
    qdrant_url: str = "http://localhost:6333"
    rag_collection: str = _DEFAULT_RAG_COLLECTION
    rag_index_version: str = _DEFAULT_RAG_INDEX_VERSION
    rag_top_k: int = int(_DEFAULT_RAG_TOP_K)
    # 分层证据门槛（按 48 题评估集实测）：相关题首位相似度最低约 0.554，
    # 无关题最高约 0.581，单阈值无法分开；因此"向量达标"或"关键词强命中"二者取一。
    rag_min_score: float = float(_DEFAULT_RAG_MIN_SCORE)
    rag_bm25_strong_score: float = float(_DEFAULT_RAG_BM25_STRONG_SCORE)
    rag_max_per_document: int = int(_DEFAULT_RAG_MAX_PER_DOCUMENT)
    rag_cache_enabled: bool = True
    rag_cache_ttl_seconds: float = float(_DEFAULT_RAG_CACHE_TTL_SECONDS)
    rag_cache_max_entries: int = int(_DEFAULT_RAG_CACHE_MAX_ENTRIES)
    rag_breaker_failure_threshold: int = int(_DEFAULT_RAG_BREAKER_FAILURE_THRESHOLD)
    rag_breaker_reset_seconds: float = float(_DEFAULT_RAG_BREAKER_RESET_SECONDS)
    rag_max_concurrency: int = int(_DEFAULT_RAG_MAX_CONCURRENCY)
    rag_max_queue: int = int(_DEFAULT_RAG_MAX_QUEUE)
    rag_queue_wait_ms: float = float(_DEFAULT_RAG_QUEUE_WAIT_MS)
    rag_user_limit: int = int(_DEFAULT_RAG_USER_LIMIT)
    rag_user_window_seconds: float = float(_DEFAULT_RAG_USER_WINDOW_SECONDS)
    rag_global_limit: int = int(_DEFAULT_RAG_GLOBAL_LIMIT)
    rag_redis_url: str = _DEFAULT_RAG_REDIS_URL
    # None = 不重排；local = 本地 cross-encoder；http = 外部 TEI 兼容服务。
    rerank_provider: str = _DEFAULT_RERANK_PROVIDER
    rerank_model_path: str = _DEFAULT_RERANK_MODEL_PATH
    rerank_url: str = ""
    rerank_model_name: str = ""
    rerank_token: str = ""
    rerank_timeout_seconds: float = float(_DEFAULT_RERANK_TIMEOUT_SECONDS)
    rerank_candidates: int = int(_DEFAULT_RERANK_CANDIDATES)
    rerank_min_score: float = float(_DEFAULT_RERANK_MIN_SCORE)
    rerank_max_length: int = int(_DEFAULT_RERANK_MAX_LENGTH)
    rerank_batch_size: int = int(_DEFAULT_RERANK_BATCH_SIZE)
    # 0 表示交给 torch 默认线程数；CPU 推理时限制线程可避免和 Embedding 抢核。
    rerank_threads: int = 0
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "bge-m3"
    # 在线检索的 Embedding 超时；离线入库仍用较长超时，两者不要共用一个值。
    ollama_timeout_seconds: float = 8.0
    # Ollama 空闲 5 分钟即卸载模型，重新加载 bge-m3 要 5~6s，正好卡在 Java 6s 读超时上，
    # 表现为"隔一会儿问第一句就没有来源"。显式保活，0/空 表示不发送该参数。
    ollama_keep_alive: str = "30m"
    knowledge_index_dir: str = str(_PROJECT_ROOT / "knowledge" / "index")
    # ---- 业务后端（M1 起 Python 自己承载业务，不再回调 Java）----
    app_env: str = "development"
    # 与 Java 同一个 MySQL 库；驱动用 asyncmy，URL 形如
    # mysql+asyncmy://user:pass@host:3306/calorie_tracker
    database_url: str = ""
    # 认证会话专用 Redis；与 RAG 限流用的 rag_redis_url 分开，避免互相顶掉配置。
    redis_url: str = ""
    jwt_secret: str = ""
    jwt_expiration_ms: int = 1_800_000
    jwt_refresh_expiration_ms: int = 2_592_000_000
    # 切流后新会话写在新命名空间，旧 Java 会话不受影响；要无感迁移须另做兼容测试。
    auth_session_namespace: str = "auth:session:"
    # ---- AI 问答（`/api/ai/**`）----
    # 对齐 Java `features.ai-chat.enabled`：关掉后三个端点直接 404，而不是返回空回答。
    ai_chat_enabled: bool = True
    # 配额数值沿用 Java `ai.quota`：正常用户一天 8~15 次，演示日（彩排+演示+老师试）约 50 次。
    ai_daily_limit: int = 50
    # 分钟窗口只防脚本：正常对话不会一分钟连问 10 句。
    ai_quota_per_minute: int = 10
    # 管理员免次数、不免 token 成本（成本上限首版未落地，语义先固定在这里）。
    ai_quota_admin_exempt: bool = True
    # 会话摘要：够 50 条消息才触发，一次压 20 条，摘要上限 1200 字。
    ai_summary_trigger_messages: int = 50
    ai_summary_batch_size: int = 20
    ai_summary_max_chars: int = 1200
    # ---- 社区写操作限流（与 Java `community.rate-limit` 默认值一致）----
    # 这些是安全边界：Redis 不可用时 fail-closed（503），不能像 AI 配额那样放行。
    community_posts_per_hour: int = 20
    community_comments_per_minute: int = 30
    community_comments_per_day: int = 500
    community_images_per_hour: int = 100
    community_image_bytes_per_day: int = 500 * 1024 * 1024
    community_reports_per_day: int = 30
    # ---- 图片对象存储（MinIO，S3 兼容）----
    # 与 Java `minio.*` 完全同一套环境变量，切流时部署脚本不用改。
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "orange-diary"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip(),
            deepseek_timeout_seconds=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60")),
            deepseek_input_price_per_million=float(
                os.getenv("DEEPSEEK_INPUT_PRICE_PER_MILLION", "0")
            ),
            deepseek_output_price_per_million=float(
                os.getenv("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", "0")
            ),
            model_retry_max_attempts=int(os.getenv("MODEL_RETRY_MAX_ATTEMPTS", "3")),
            model_retry_base_delay_seconds=float(
                os.getenv("MODEL_RETRY_BASE_DELAY_SECONDS", "0.5")
            ),
            model_retry_backoff_factor=float(
                os.getenv("MODEL_RETRY_BACKOFF_FACTOR", "2.0")
            ),
            model_retry_max_delay_seconds=float(
                os.getenv("MODEL_RETRY_MAX_DELAY_SECONDS", "5.0")
            ),
            model_breaker_failure_threshold=int(
                os.getenv("MODEL_BREAKER_FAILURE_THRESHOLD", "3")
            ),
            model_breaker_reset_seconds=float(
                os.getenv("MODEL_BREAKER_RESET_SECONDS", "30.0")
            ),
            ai_service_token=os.getenv("AI_SERVICE_TOKEN", "").strip(),
            rag_enabled=os.getenv("RAG_ENABLED", "true").strip().lower() not in {"0", "false", "no"},
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/"),
            rag_collection=os.getenv("RAG_COLLECTION", _DEFAULT_RAG_COLLECTION).strip(),
            rag_index_version=os.getenv("RAG_INDEX_VERSION", _DEFAULT_RAG_INDEX_VERSION).strip(),
            rag_top_k=int(os.getenv("RAG_TOP_K", _DEFAULT_RAG_TOP_K)),
            rag_min_score=float(os.getenv("RAG_MIN_SCORE", _DEFAULT_RAG_MIN_SCORE)),
            rag_bm25_strong_score=float(
                os.getenv("RAG_BM25_STRONG_SCORE", _DEFAULT_RAG_BM25_STRONG_SCORE)
            ),
            rag_max_per_document=int(
                os.getenv("RAG_MAX_PER_DOCUMENT", _DEFAULT_RAG_MAX_PER_DOCUMENT)
            ),
            rag_max_concurrency=int(
                os.getenv("RAG_MAX_CONCURRENCY", _DEFAULT_RAG_MAX_CONCURRENCY)
            ),
            rag_max_queue=int(os.getenv("RAG_MAX_QUEUE", _DEFAULT_RAG_MAX_QUEUE)),
            rag_queue_wait_ms=float(
                os.getenv("RAG_QUEUE_WAIT_MS", _DEFAULT_RAG_QUEUE_WAIT_MS)
            ),
            rag_user_limit=int(os.getenv("RAG_USER_LIMIT", _DEFAULT_RAG_USER_LIMIT)),
            rag_user_window_seconds=float(
                os.getenv("RAG_USER_WINDOW_SECONDS", _DEFAULT_RAG_USER_WINDOW_SECONDS)
            ),
            rag_global_limit=int(os.getenv("RAG_GLOBAL_LIMIT", _DEFAULT_RAG_GLOBAL_LIMIT)),
            rag_redis_url=os.getenv("RAG_REDIS_URL", _DEFAULT_RAG_REDIS_URL).strip(),
            rag_cache_enabled=os.getenv("RAG_CACHE_ENABLED", _DEFAULT_RAG_CACHE_ENABLED)
            .strip()
            .lower()
            not in {"0", "false", "no"},
            rag_cache_ttl_seconds=float(
                os.getenv("RAG_CACHE_TTL_SECONDS", _DEFAULT_RAG_CACHE_TTL_SECONDS)
            ),
            rag_cache_max_entries=int(
                os.getenv("RAG_CACHE_MAX_ENTRIES", _DEFAULT_RAG_CACHE_MAX_ENTRIES)
            ),
            rag_breaker_failure_threshold=int(
                os.getenv("RAG_BREAKER_FAILURE_THRESHOLD", _DEFAULT_RAG_BREAKER_FAILURE_THRESHOLD)
            ),
            rag_breaker_reset_seconds=float(
                os.getenv("RAG_BREAKER_RESET_SECONDS", _DEFAULT_RAG_BREAKER_RESET_SECONDS)
            ),
            rerank_provider=os.getenv("RERANK_PROVIDER", _DEFAULT_RERANK_PROVIDER).strip().lower(),
            rerank_model_path=os.getenv("RERANK_MODEL_PATH", _DEFAULT_RERANK_MODEL_PATH).strip(),
            rerank_url=os.getenv("RERANK_URL", "").strip(),
            rerank_model_name=os.getenv("RERANK_MODEL_NAME", "").strip(),
            rerank_token=os.getenv("RERANK_TOKEN", "").strip(),
            rerank_timeout_seconds=float(
                os.getenv("RERANK_TIMEOUT_SECONDS", _DEFAULT_RERANK_TIMEOUT_SECONDS)
            ),
            rerank_candidates=int(
                os.getenv("RERANK_CANDIDATES", _DEFAULT_RERANK_CANDIDATES)
            ),
            rerank_min_score=float(os.getenv("RERANK_MIN_SCORE", _DEFAULT_RERANK_MIN_SCORE)),
            rerank_max_length=int(
                os.getenv("RERANK_MAX_LENGTH", _DEFAULT_RERANK_MAX_LENGTH)
            ),
            rerank_batch_size=int(os.getenv("RERANK_BATCH_SIZE", _DEFAULT_RERANK_BATCH_SIZE)),
            rerank_threads=int(os.getenv("RERANK_THREADS", "0")),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8.0")),
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip(),
            ollama_embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3").strip(),
            knowledge_index_dir=os.getenv(
                "KNOWLEDGE_INDEX_DIR", str(_PROJECT_ROOT / "knowledge" / "index")
            ),
            app_env=os.getenv("APP_ENV", "development").strip().lower(),
            database_url=os.getenv("DATABASE_URL", "").strip(),
            redis_url=os.getenv("REDIS_URL", "").strip(),
            jwt_secret=os.getenv("JWT_SECRET", "").strip(),
            jwt_expiration_ms=int(os.getenv("JWT_EXPIRATION_MS", "1800000")),
            jwt_refresh_expiration_ms=int(os.getenv("JWT_REFRESH_EXPIRATION_MS", "2592000000")),
            auth_session_namespace=os.getenv("AUTH_SESSION_NAMESPACE", "auth:session:").strip(),
            community_posts_per_hour=int(os.getenv("COMMUNITY_POSTS_PER_HOUR", "20")),
            community_comments_per_minute=int(os.getenv("COMMUNITY_COMMENTS_PER_MINUTE", "30")),
            community_comments_per_day=int(os.getenv("COMMUNITY_COMMENTS_PER_DAY", "500")),
            community_images_per_hour=int(os.getenv("COMMUNITY_IMAGES_PER_HOUR", "100")),
            community_image_bytes_per_day=int(
                os.getenv("COMMUNITY_IMAGE_BYTES_PER_DAY", str(500 * 1024 * 1024))
            ),
            community_reports_per_day=int(os.getenv("COMMUNITY_REPORTS_PER_DAY", "30")),
            minio_endpoint=os.getenv("MINIO_ENDPOINT", "http://localhost:9000").strip(),
            minio_access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin").strip(),
            minio_secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin").strip(),
            minio_bucket=os.getenv("MINIO_BUCKET", "orange-diary").strip(),
            ai_chat_enabled=os.getenv("FEATURES_AI_CHAT_ENABLED", "true").strip().lower()
            not in {"0", "false", "no"},
            ai_daily_limit=int(os.getenv("AI_DAILY_QUOTA", "50")),
            ai_quota_per_minute=int(os.getenv("AI_QUOTA_PER_MINUTE", "10")),
            ai_quota_admin_exempt=os.getenv("AI_QUOTA_ADMIN_EXEMPT", "true").strip().lower()
            not in {"0", "false", "no"},
            ai_summary_trigger_messages=int(os.getenv("AI_SUMMARY_TRIGGER_MESSAGES", "50")),
            ai_summary_batch_size=int(os.getenv("AI_SUMMARY_BATCH_SIZE", "20")),
            ai_summary_max_chars=int(os.getenv("AI_SUMMARY_MAX_CHARS", "1200")),
        )
