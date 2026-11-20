"""图片对象存储（对齐 `ImageStorageServiceImpl`，MinIO / S3 兼容）。

三条与安全直接相关的规则，改任何一条都会打开一个口子：

1. **只认四种 MIME**：jpeg/png/webp/gif。缩略图链路和前端 `<img>` 都靠它。
2. **头部魔数必须与声明的 MIME 一致**：只看 `Content-Type` 的话，把 .exe 改名成
   .png 再伪造请求头就能过——浏览器拿到 `image/png` 会 sniff，等于挂了个
   任意内容分发点。
3. **返回的 key 里带用户 ID**：`posts/{uid}/...`、`avatars/{uid}/...`。发帖时
   用前缀校验归属，头像用 `avatar_upload` 登记表校验，两道都不依赖客户端自述。

MinIO 客户端是同步的（urllib3），在 async 路由里必须丢线程池，否则一次上传
就把整个事件循环钉住。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO

from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.core.errors import BusinessError

# 与 Java 同值：普通图片 10MB、头像 5MB。Spring 的 multipart 上限也是 10MB，
# 两边一致才能在切流前后给出同样的报错。
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_AVATAR_BYTES = 5 * 1024 * 1024

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")

_EXTENSIONS = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

_APP_ZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass(frozen=True)
class UploadedImage:
    key: str
    content_type: str


def extension_for(content_type: str) -> str:
    return _EXTENSIONS.get(content_type, ".jpg")


def matches_image_type(header: bytes, content_type: str) -> bool:
    """按魔数判定真实格式。header 至少 12 字节，不足直接判假。"""
    if len(header) < 12:
        return False
    if content_type == "image/png":
        return header[:8] == b"\x89PNG\r\n\x1a\n"
    if content_type == "image/jpeg":
        return header[0] == 0xFF and header[1] == 0xD8 and header[2] == 0xFF
    if content_type == "image/gif":
        return header[:6] in (b"GIF87a", b"GIF89a")
    if content_type == "image/webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def validate_upload(
    *, size: int, content_type: str | None, header: bytes, avatar: bool
) -> None:
    """在写对象存储之前把不合法的情况全部挡掉，顺序与 Java 一致。"""
    if size <= 0:
        raise BusinessError(400, "请选择图片")
    if size > MAX_IMAGE_BYTES:
        raise BusinessError(400, "单张图片不能超过10MB")
    if not content_type or content_type not in ALLOWED_CONTENT_TYPES:
        raise BusinessError(400, "仅支持 JPG、PNG、WEBP 或 GIF 图片")
    if avatar and (size > MAX_AVATAR_BYTES or content_type == "image/gif"):
        raise BusinessError(400, "头像仅支持不超过5MB的 JPG、PNG、WEBP 图片")
    if not matches_image_type(header, content_type):
        raise BusinessError(400, "图片内容与格式不匹配")


def build_object_key(*, user_id: int, content_type: str, avatar: bool) -> str:
    prefix = "avatars/" if avatar else "posts/"
    # 日期分目录不是为了分区，是为了人肉排查时能按天圈定范围。
    day = datetime.now(_APP_ZONE).date().isoformat()
    return f"{prefix}{user_id}/{day}/{uuid.uuid4()}{extension_for(content_type)}"


class ImageStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None
    # ---------------- 懒加载客户端 ----------------

    def _minio(self):
        if self._client is None:
            from minio import Minio

            self._client = Minio(
                self._settings.minio_endpoint.replace("http://", "").replace("https://", ""),
                access_key=self._settings.minio_access_key,
                secret_key=self._settings.minio_secret_key,
                secure=self._settings.minio_endpoint.startswith("https://"),
            )
        return self._client

    def _bucket(self) -> str:
        return self._settings.minio_bucket

    # ---------------- 上传 / 读取 / 删除 ----------------

    async def upload(
        self, *, data: bytes, content_type: str, user_id: int, avatar: bool = False
    ) -> UploadedImage:
        key = build_object_key(user_id=user_id, content_type=content_type, avatar=avatar)
        await run_in_threadpool(self._put, key, data, content_type)
        return UploadedImage(key=key, content_type=content_type)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            client = self._minio()
            bucket = self._bucket()
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            client.put_object(
                bucket, key, BytesIO(data), length=len(data), content_type=content_type
            )
        except BusinessError:
            raise
        except Exception as exc:
            # 上传失败一律 503：能不能重试取决于存储是否恢复，不该让用户以为图有问题。
            raise BusinessError(503, "图片上传失败，请稍后重试") from exc

    async def content_type_of(self, key: str) -> str:
        return await run_in_threadpool(self._stat_content_type, key)

    def _stat_content_type(self, key: str) -> str:
        try:
            stat = self._minio().stat_object(self._bucket(), key)
        except Exception as exc:
            raise BusinessError(404, "图片不存在") from exc
        return stat.content_type

    async def open_bytes(self, key: str) -> bytes:
        return await run_in_threadpool(self._get, key)

    def _get(self, key: str) -> bytes:
        response = None
        try:
            response = self._minio().get_object(self._bucket(), key)
            return response.read()
        except BusinessError:
            raise
        except Exception as exc:
            raise BusinessError(404, "图片不存在") from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    async def delete(self, key: str) -> None:
        # 删除是"尽力而为"：帖子已经删了，对象没删掉只会留下孤儿文件，
        # 不该因为这个让删帖接口报错。
        try:
            await run_in_threadpool(self._remove, key)
        except Exception:  # pragma: no cover - 清理路径
            pass

    def _remove(self, key: str) -> None:
        self._minio().remove_object(self._bucket(), key)


_storages: dict[tuple[str, str, str, str], ImageStorage] = {}


def get_image_storage(settings: Settings) -> ImageStorage:
    """按配置复用实例，避免每个请求都新建一个 MinIO 客户端（各自一套连接池）。"""
    key = (
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
    )
    storage = _storages.get(key)
    if storage is None:
        storage = ImageStorage(settings)
        _storages[key] = storage
    return storage
