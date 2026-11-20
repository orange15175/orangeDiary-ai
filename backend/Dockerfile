# AI 服务镜像：与 requirements.txt 固定版本 + Python 3.13 对齐（本机实测通过的组合）。
FROM python:3.13-slim

# 不写 .pyc（容器内是只读层，写了也没用，还平白多一层垃圾）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖先装：改代码不会让这一层缓存失效
COPY requirements.txt .
RUN pip install -r requirements.txt

# 非 root 运行：容器被攻破时不给宿主留直接落地的权限
RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser app ./app
# 索引随镜像走（约 3MB），模型权重不进镜像（1.1GB，由 compose 挂卷）
COPY --chown=appuser:appuser knowledge ./knowledge

USER appuser
EXPOSE 8000

# 就绪探针：/health 能返回 200 才算起来（模型预热在后台线程，不影响存活判定）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
