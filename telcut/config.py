import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


def number(value, name, lower, upper=None):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < lower or (upper is not None and value > upper)):
        raise ValueError(f"{name} 数值无效")
    return value


def ids(value, name):
    if not isinstance(value, list) or any(type(x) is not int or x == 0 for x in value):
        raise ValueError(f"{name} 必须是非零整数 ID 数组")
    return tuple(set(value))


@dataclass(frozen=True)
class CaptureConfig:
    display: int = 1
    right_width_ratio: float = 0.5
    top_height_ratio: float = 0.04

    def __post_init__(self):
        if type(self.display) is not int or self.display < 1:
            raise ValueError("capture.display 必须是从 1 开始的屏幕编号")
        number(self.right_width_ratio, "capture.right_width_ratio", 0.01, 1)
        number(self.top_height_ratio, "capture.top_height_ratio", 0.005, 1)


@dataclass(frozen=True)
class Config:
    chat_ids: Tuple[int, ...] = ()
    sender_ids: Tuple[int, ...] = ()
    keywords: Tuple[str, ...] = ()
    cooldown_seconds: float = 30
    max_message_age_seconds: float = 120
    caption: str = ""
    capture: CaptureConfig = field(default_factory=CaptureConfig)

    @classmethod
    def load(cls, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"未知配置项: {', '.join(sorted(unknown))}")
        keywords = data.get("keywords", [])
        if (not isinstance(keywords, list)
                or any(not isinstance(k, str) or not k.strip() for k in keywords)):
            raise ValueError("keywords 必须是非空字符串数组；[] 表示所有消息")
        caption = data.get("caption", "")
        if not isinstance(caption, str) or len(caption) > 1024:
            raise ValueError("caption 必须是长度不超过 1024 的字符串")
        raw_capture = data.get("capture", {})
        if not isinstance(raw_capture, dict):
            raise ValueError("capture 必须是 JSON 对象")
        try:
            capture = CaptureConfig(**raw_capture)
        except TypeError as exc:
            raise ValueError("capture 存在未知配置项") from exc
        return cls(
            chat_ids=ids(data.get("chat_ids", []), "chat_ids"),
            sender_ids=ids(data.get("sender_ids", []), "sender_ids"),
            keywords=tuple(k.strip().casefold() for k in keywords),
            cooldown_seconds=number(data.get("cooldown_seconds", 30), "cooldown_seconds", 0),
            max_message_age_seconds=number(data.get("max_message_age_seconds", 120),
                                           "max_message_age_seconds", 1),
            caption=caption, capture=capture,
        )
