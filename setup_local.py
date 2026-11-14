"""Interactive local setup; credentials stay on this computer."""
import asyncio
import getpass
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from telcut.capture import MenuBarHiddenError, capture_clock
from telcut.cli import session_lock, telegram_command
from telcut.config import Config

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".telcut"


def status(value, message=""):
    STATE.mkdir(mode=0o700, exist_ok=True)
    (STATE / "status.json").write_text(
        json.dumps({"status": value, "message": message, "pid": os.getpid()}, ensure_ascii=False),
        encoding="utf-8",
    )


def prepare_credentials():
    path = ROOT / ".env"
    values = dotenv_values(path) if path.exists() else {}
    api_id = os.environ.get("TELEGRAM_API_ID") or values.get("TELEGRAM_API_ID", "")
    api_hash = os.environ.get("TELEGRAM_API_HASH") or values.get("TELEGRAM_API_HASH", "")
    valid_id = lambda value: bool(value and value.isdigit() and int(value) > 0)
    valid_hash = lambda value: bool(value and len(value) == 32
                                  and all(c in "0123456789abcdefABCDEF" for c in value))
    if valid_id(api_id) and valid_hash(api_hash):
        return
    status("needs_api_credentials")
    print("\n首次设置：请在 https://my.telegram.org 的 API development tools 获取自己的 api_id 和 api_hash。")
    print("请直接在这个终端输入；输入的 api_hash 不会显示，也不需要发到聊天中。")
    while not valid_id(api_id):
        api_id = input("api_id（数字）: ").strip()
    while not valid_hash(api_hash):
        api_hash = getpass.getpass("api_hash（32位，不回显）: ").strip()
    # Values are validated as digits / hex before writing dotenv syntax.
    path.write_text(f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\n", encoding="utf-8")
    path.chmod(0o600)


def prepare_capture(config):
    while True:
        try:
            png = capture_clock(config.capture)
            preview = ROOT / "preview.png"
            preview.write_bytes(png)
            print(f"\n系统时间截图预览：{preview}")
            subprocess.run(["/usr/bin/open", str(preview)], check=False)
            return
        except MenuBarHiddenError as exc:
            status("needs_visible_menu_bar", str(exc))
            print(f"\n{exc}")
            input("菜单栏可见后按回车重试；也可按 Ctrl+C 退出: ")
        except RuntimeError as exc:
            status("needs_screen_permission", str(exc))
            print(f"\n{exc}")
            print("请为 Terminal（终端）开启屏幕录制权限。必要时退出终端，再双击“启动telCut.command”。")
            subprocess.run(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"],
                           check=False)
            input("权限已开启后按回车重试；也可按 Ctrl+C 退出: ")


def ready():
    status("running")
    print("\ntelCut 已启动：在 Telegram 中发送 telCut，会在同一聊天发送菜单栏时间图片。"
          "不附文字、不引用原消息。保持本窗口运行，按 Ctrl+C 停止。", flush=True)


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    try:
        with session_lock(STATE / "setup"):
            config = Config.load(ROOT / "config.json")
            print("telCut 菜单栏截图\n匹配范围：所有聊天；包括你自己发出的指令\n完整指令：")
            for keyword in config.keywords:
                print(keyword)
            prepare_credentials()
            status("needs_telegram_login")
            asyncio.run(telegram_command("login", config, ROOT))
            prepare_capture(config)
            status("starting")
            asyncio.run(telegram_command("run", config, ROOT, on_ready=ready))
            status("stopped")
    except KeyboardInterrupt:
        status("stopped")
        print("\n已停止。")
    except Exception as exc:
        status("error", str(exc))
        print(f"\n启动失败：{exc}", file=sys.stderr)
        input("按回车关闭窗口: ")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
