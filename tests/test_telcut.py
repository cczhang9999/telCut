import asyncio
import io
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from telcut.capture import capture_clock, crop_clock
from telcut.cli import session_lock, telegram_command
from telcut.config import CaptureConfig, Config
from telcut.service import AutoReply, Incoming
from telcut.state import State


class ConfigTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return Config.load(path)

    def test_rules_and_case_normalization(self):
        config = self.load({"chat_ids": [-100123], "keywords": [" Check ", "查岗"]})
        self.assertEqual(config.chat_ids, (-100123,))
        self.assertEqual(config.keywords, ("check", "查岗"))

    def test_reject_invalid_config(self):
        for data in ({"chat_ids": [True]}, {"chat_ids": ["123"]},
                     {"keywords": [""]}, {"cooldown_seconds": -1},
                     {"cooldown_seconds": float("nan")},
                     {"capture": {"top_height_ratio": 0}},
                     {"capture": {"display": True}},
                     {"caption": 42}, {"typo": 1}, []):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)


class CaptureTests(unittest.TestCase):
    def test_retina_crop_preserves_actual_top_right_pixels(self):
        for scale in (1, 2):
            image = Image.new("RGB", (1000 * scale, 800 * scale), "red")
            image.putpixel((999 * scale, 0), (0, 255, 0))
            crop = crop_clock(image, CaptureConfig())
            self.assertEqual(crop.size, (500 * scale, 32 * scale))
            self.assertEqual(crop.getpixel((499 * scale, 0)), (0, 255, 0))

    def test_capture_returns_only_crop_and_removes_full_image(self):
        captured_paths = []

        def fake_capture(args, **kwargs):
            path = Path(args[-1])
            captured_paths.append(path)
            Image.new("RGB", (1000, 800), "blue").save(path)
            return type("Result", (), {"returncode": 0})()

        with patch("telcut.capture.sys.platform", "darwin"), \
                patch("telcut.capture.subprocess.run", side_effect=fake_capture):
            png = capture_clock(CaptureConfig())
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (500, 32))
        self.assertFalse(captured_paths[0].exists())
        self.assertFalse(captured_paths[0].parent.exists())

    def test_permission_failure_raises_without_upload(self):
        with patch("telcut.capture.sys.platform", "darwin"), \
                patch("telcut.capture.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, "屏幕录制"):
                capture_clock(CaptureConfig())


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.state = State(self.path)
        self.config = Config(chat_ids=(123,), cooldown_seconds=30)
        self.now = 1000.0
        self.sent = []
        self.captured = 0

        def capture(config):
            self.captured += 1
            return b"png"

        async def send(message, file, caption):
            self.sent.append((message, file.read(), file.name, caption))

        self.capture, self.send = capture, send
        self.reply = AutoReply(self.config, self.state, send, capture, lambda: self.now)
        self.message = Incoming(123, 1, 456, "查岗 CHECK", 1000)

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    async def test_send_png_to_original_message(self):
        self.assertTrue(await self.reply.handle(self.message))
        self.assertEqual(self.sent, [(self.message, b"png", "system-time.png", "")])

    async def test_ignored_messages_never_capture(self):
        for changes in ({"chat_id": 999}, {"outgoing": True}, {"bot": True},
                        {"broadcast": True}, {"service": True},
                        {"timestamp": 0}, {"timestamp": 1100}):
            self.assertFalse(await self.reply.handle(replace(self.message, **changes)))
        self.assertEqual(self.captured, 0)

    async def test_keywords_and_sender_whitelist(self):
        self.reply.config = replace(self.config, keywords=("check",), sender_ids=(456,))
        self.assertFalse(await self.reply.handle(replace(self.message, text="hello")))
        self.assertFalse(await self.reply.handle(replace(self.message, sender_id=789)))
        self.assertTrue(await self.reply.handle(self.message))

    async def test_captionless_media_allowed_without_keywords(self):
        self.assertTrue(await self.reply.handle(replace(self.message, text="")))

    async def test_concurrent_events_send_once(self):
        result = await asyncio.gather(*(self.reply.handle(self.message) for _ in range(5)))
        self.assertEqual(result.count(True), 1)
        self.assertEqual(self.captured, 1)

    async def test_cooldown_skip_is_not_replayed_later(self):
        await self.reply.handle(self.message)
        self.now = 1010
        second = replace(self.message, message_id=2, timestamp=1010)
        self.assertFalse(await self.reply.handle(second))
        self.now = 1040
        self.assertFalse(await self.reply.handle(second))
        self.assertTrue(await self.reply.handle(replace(second, message_id=3, timestamp=1040)))

    async def test_cooldown_is_per_chat(self):
        self.reply.config = replace(self.config, chat_ids=(123, 789))
        self.assertTrue(await self.reply.handle(self.message))
        self.assertTrue(await self.reply.handle(replace(self.message, chat_id=789)))

    async def test_persistent_dedupe_survives_restart(self):
        await self.reply.handle(self.message)
        self.state.close()
        self.state = State(self.path)
        restarted = AutoReply(self.config, self.state, self.send, self.capture, lambda: self.now)
        self.now = 1040
        self.assertFalse(await restarted.handle(self.message))
        self.assertEqual(len(self.sent), 1)

    async def test_failed_capture_never_sends_or_retries_same_event(self):
        def fail(_):
            raise RuntimeError("permission denied")

        self.reply.capture = fail
        self.assertFalse(await self.reply.handle(self.message))
        self.now = 1040
        self.assertFalse(await self.reply.handle(self.message))
        self.assertEqual(self.sent, [])

    async def test_failed_upload_does_not_resend_uncertain_result(self):
        async def fail(*args):
            raise TimeoutError("uncertain upload result")

        self.reply.send = fail
        self.assertFalse(await self.reply.handle(self.message))
        self.now = 1040
        self.reply.send = self.send
        self.assertFalse(await self.reply.handle(self.message))
        self.assertEqual(self.captured, 1)

    async def test_flood_wait_pauses_other_chats(self):
        class FloodWait(Exception):
            seconds = 60

        async def limited(*args):
            raise FloodWait()

        self.reply.config = replace(self.config, chat_ids=(123, 789))
        self.reply.send = limited
        self.assertFalse(await self.reply.handle(self.message))
        self.reply.send = self.send
        second = replace(self.message, chat_id=789, message_id=2)
        self.assertFalse(await self.reply.handle(second))
        self.now = 1061
        self.assertTrue(await self.reply.handle(replace(second, message_id=3, timestamp=1061)))


class LockTests(unittest.TestCase):
    def test_same_session_cannot_run_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            with session_lock(Path(directory)):
                with self.assertRaises(RuntimeError):
                    with session_lock(Path(directory)):
                        pass


class TelegramAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_incoming_event_uploads_real_crop_to_original_chat_and_reply(self):
        now = time.time()
        uploaded = []
        options = {}

        class FakeClient:
            async def start(self):
                pass

            async def disconnect(self):
                options["disconnected"] = True

            async def iter_dialogs(self):
                yield SimpleNamespace(id=123, input_entity="target-peer")

            def on(self, builder):
                def register(callback):
                    self.callback = callback
                    return callback
                return register

            async def send_file(self, entity, file, **kwargs):
                uploaded.append((entity, file.read(), kwargs))

            async def run_until_disconnected(self):
                async def get_sender():
                    return SimpleNamespace(bot=False)

                event = SimpleNamespace(
                    is_channel=False, is_group=False, chat_id=123, id=42,
                    sender_id=456, raw_text="查岗", out=False,
                    date=SimpleNamespace(timestamp=lambda: now),
                    get_sender=get_sender, message=SimpleNamespace(action=None),
                )
                await self.callback(event)

        def client_factory(*args, **kwargs):
            options.update(kwargs)
            return FakeClient()

        def capture(args, **kwargs):
            Image.new("RGB", (1000, 800), "green").save(args[-1])
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory, \
                patch("telcut.cli.credentials", return_value=(12345, "a" * 32)), \
                patch("telcut.cli.TelegramClient", side_effect=client_factory), \
                patch("telcut.capture.sys.platform", "darwin"), \
                patch("telcut.capture.subprocess.run", side_effect=capture):
            await telegram_command("run", Config(chat_ids=(123,)), Path(directory))
        self.assertEqual(len(uploaded), 1)
        entity, png, kwargs = uploaded[0]
        self.assertEqual(entity, "target-peer")
        self.assertEqual(kwargs["reply_to"], 42)
        self.assertTrue(kwargs["force_document"])
        self.assertIsNone(kwargs["parse_mode"])
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (500, 32))
        self.assertFalse(options["catch_up"])
        self.assertEqual(options["flood_sleep_threshold"], 0)
        self.assertTrue(options["disconnected"])
