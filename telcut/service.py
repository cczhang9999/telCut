import asyncio
import io
import logging
import time
from dataclasses import dataclass

from .capture import capture_clock
from .config import Config
from .state import State

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Incoming:
    chat_id: int
    message_id: int
    sender_id: int
    text: str
    timestamp: float
    outgoing: bool = False
    broadcast: bool = False
    bot: bool = False
    service: bool = False


class AutoReply:
    def __init__(self, config: Config, state: State, send, capture=capture_clock, clock=time.time):
        self.config, self.state, self.send = config, state, send
        self.capture, self.clock = capture, clock
        self.lock = asyncio.Lock()
        self.blocked_until = 0.0

    def matches(self, message: Incoming, now: float):
        age = now - message.timestamp
        return (
            not (message.outgoing or message.broadcast or message.bot or message.service)
            and message.chat_id in self.config.chat_ids
            and (not self.config.sender_ids or message.sender_id in self.config.sender_ids)
            and -5 <= age <= self.config.max_message_age_seconds
            and (not self.config.keywords
                 or any(k in message.text.casefold() for k in self.config.keywords))
        )

    async def handle(self, message: Incoming):
        if not self.matches(message, self.clock()):
            return False
        async with self.lock:
            now = self.clock()
            # Recheck after waiting: never send a stale queued screenshot.
            if (not self.matches(message, now) or now < self.blocked_until
                    or not self.state.claim(message.chat_id, message.message_id, now,
                                            self.config.cooldown_seconds)):
                return False
            try:
                png = await asyncio.to_thread(self.capture, self.config.capture)
                file = io.BytesIO(png)
                file.name = "system-time.png"
                await self.send(message, file, self.config.caption)
            except Exception as exc:
                # FloodWait errors expose seconds; back off globally without
                # blocking the update loop or retrying an outdated screenshot.
                wait = getattr(exc, "seconds", None)
                if isinstance(wait, (int, float)) and wait > 0:
                    self.blocked_until = self.clock() + wait
                    log.error("Telegram 限流，暂停自动回复 %s 秒", wait)
                else:
                    log.error("回复失败 chat=%s message=%s: %s",
                              message.chat_id, message.message_id, exc)
                self.state.finish(message.chat_id, message.message_id, "failed")
                return False
            self.state.finish(message.chat_id, message.message_id, "sent")
            log.info("已回复系统时间截图 chat=%s message=%s", message.chat_id, message.message_id)
            return True
