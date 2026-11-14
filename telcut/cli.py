import argparse
import asyncio
import fcntl
import json
import logging
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events

from .capture import capture_clock
from .config import Config
from .service import AutoReply, Incoming
from .state import State

log = logging.getLogger(__name__)


@contextmanager
def session_lock(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "process.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有 telcut 进程使用此配置，请先停止它") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def credentials(directory):
    load_dotenv(directory / ".env", override=False)
    raw_id = os.environ.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    if not raw_id.isdigit() or int(raw_id) <= 0 or len(api_hash) != 32:
        raise ValueError("请在 .env 中设置有效的 TELEGRAM_API_ID 和 TELEGRAM_API_HASH")
    if any(c not in "0123456789abcdefABCDEF" for c in api_hash):
        raise ValueError("TELEGRAM_API_HASH 必须是 32 位十六进制字符串")
    return int(raw_id), api_hash


async def telegram_command(command, config, directory):
    api_id, api_hash = credentials(directory)
    state_dir = directory / ".telcut"
    with session_lock(state_dir):
        client = TelegramClient(str(state_dir / "telegram"), api_id, api_hash,
                                catch_up=False, flood_sleep_threshold=0)
        state = None
        try:
            await client.start()
            if command == "login":
                print("Telegram 登录成功。")
                return
            if command == "chats":
                print("聊天 ID\t名称")
                async for dialog in client.iter_dialogs():
                    print(f"{dialog.id}\t{dialog.name}")
                return
            # Populate entity cache and fail clearly for inaccessible chat IDs.
            dialogs = {d.id: d async for d in client.iter_dialogs()}
            missing = set(config.chat_ids) - set(dialogs)
            if missing:
                raise ValueError(f"找不到目标聊天 {sorted(missing)}，请用 telcut chats 获取 ID")
            # Check capture permission before enabling any automatic replies.
            await asyncio.to_thread(capture_clock, config.capture)
            state = State(state_dir / "state.sqlite3")

            async def send(message, file, caption):
                await client.send_file(
                    dialogs[message.chat_id].input_entity, file,
                    caption=caption or None, reply_to=message.message_id,
                    force_document=True, parse_mode=None,
                )

            responder = AutoReply(config, state, send)

            @client.on(events.NewMessage(incoming=True, chats=list(config.chat_ids)))
            async def on_message(event):
                # Sender lookup may fail during reconnect; keep the handler alive.
                try:
                    if event.is_channel and not event.is_group:
                        return
                    sender = await event.get_sender()
                    await responder.handle(Incoming(
                        chat_id=event.chat_id, message_id=event.id,
                        sender_id=event.sender_id, text=event.raw_text or "",
                        timestamp=event.date.timestamp(), outgoing=event.out,
                        bot=bool(getattr(sender, "bot", False)),
                        service=bool(getattr(event.message, "action", None)),
                    ))
                except Exception:
                    log.exception("处理消息失败 chat=%s message=%s", event.chat_id, event.id)

            log.info("正在监听 %s 个聊天；按 Ctrl+C 停止", len(config.chat_ids))
            await client.run_until_disconnected()
        finally:
            await client.disconnect()
            if state is not None:
                state.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="收到 Telegram 消息后回复真实 macOS 系统时间截图")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="生成配置文件")
    sub.add_parser("login", help="登录 Telegram（手机号、验证码及可选两步验证密码）")
    sub.add_parser("chats", help="列出聊天 ID")
    preview = sub.add_parser("preview", help="仅保存本地截图预览，不发送消息")
    preview.add_argument("--output", type=Path, default=Path("preview.png"))
    sub.add_parser("run", help="开始自动回复")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    try:
        path = args.config.expanduser().resolve()
        if args.command == "init":
            with path.open("x", encoding="utf-8") as handle:
                json.dump(asdict(Config()), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            print(f"已创建 {path}。请配置 .env，并用 telcut chats 查询目标聊天 ID。")
            return
        config = Config.load(path)
        if args.command == "preview":
            args.output.write_bytes(capture_clock(config.capture))
            print(f"预览已保存：{args.output.resolve()}；请确认截图中日期和时间可见。")
            return
        if args.command == "run" and not config.chat_ids:
            raise ValueError("chat_ids 为空：请先运行 telcut chats，并填写至少一个目标聊天 ID")
        asyncio.run(telegram_command(args.command, config, path.parent))
    except KeyboardInterrupt:
        print("\n已停止。")
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
