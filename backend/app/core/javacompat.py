"""Java 格式化习惯的兼容函数。

只放那些"前端已经按 Java 输出去解析"的格式，避免各模块各写一份：
`LocalDateTime.toString()` 的输出跟 Python 的 `isoformat()` 并不一样，
直接换会让历史消息时间、AI 记录预览的 `recordTime` 在两版之间出现
`2026-11-18T20:30:05.123456+08:00` 与 `2026-11-18T20:30:05.123` 的差异。
"""
from __future__ import annotations

from datetime import datetime


def local_datetime_string(value: datetime | None) -> str | None:
    """复刻 `java.time.LocalDateTime.toString()`。

    规则（`LocalTime.toString()`）：秒为 0 且无小数时只输出到分钟；有小数时
    小数部分按 3 / 6 / 9 位补齐。这里的时间精度是微秒，所以只会出现 3 位或 6 位。
    """
    if value is None:
        return None
    text = value.strftime("%Y-%m-%dT%H:%M")
    if value.second or value.microsecond:
        text += f":{value.second:02d}"
    if value.microsecond:
        if value.microsecond % 1000 == 0:
            text += f".{value.microsecond // 1000:03d}"
        else:
            text += f".{value.microsecond:06d}"
    return text
