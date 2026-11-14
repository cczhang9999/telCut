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

from PIL import Image, ImageDraw

from telcut.capture import capture_clock, crop_clock, has_menu_content
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

    def test_all_chats_requires_nonempty_keywords_and_real_boolean(self):
        for data in ({"all_chats": True}, {"all_chats": "true"},
                     {"all_chats": 1, "keywords": ["查岗"]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)
        self.assertTrue(self.load({"all_chats": True, "keywords": ["查岗"]}).all_chats)

    def test_reject_invalid_command_options(self):
        for data in ({"trigger_mode": "unknown"}, {"include_outgoing": "yes"},
                     {"quote_reply": 0}, {"send_as_document": None},
                     {"capture": {"width_pixels": True}},
                     {"capture": {"height_pixels": -1}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.load(data)


class CaptureTests(unittest.TestCase):
    def test_blank_menu_is_rejected_but_visible_clock_text_is_accepted(self):
        image = Image.new("RGB", (868, 62), (233, 225, 210))
        ImageDraw.Draw(image).rectangle((0, 48, 867, 61), fill=(160, 150, 140))
        self.assertFalse(has_menu_content(image))
        ImageDraw.Draw(image).text((620, 14), "11/14 20:18", fill="black")
        self.assertTrue(has_menu_content(image))

    def test_reference_sized_crop_takes_actual_top_right_region(self):
        image = Image.new("RGB", (2880, 1800), "white")
        image.putpixel((2012, 0), (1, 2, 3))
        image.putpixel((2879, 61), (4, 5, 6))
        crop = crop_clock(image, CaptureConfig(width_pixels=868, height_pixels=62))
        self.assertEqual(crop.size, (868, 62))
        self.assertEqual(crop.getpixel((0, 0)), (1, 2, 3))
        self.assertEqual(crop.getpixel((867, 61)), (4, 5, 6))

    def test_pixel_crop_clamps_to_small_display_without_padding(self):
        crop = crop_clock(Image.new("RGB", (400, 40)),
                          CaptureConfig(width_pixels=868, height_pixels=62))
        self.assertEqual(crop.size, (400, 40))

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

    async def test_all_chats_matches_only_full_notice_ignoring_line_breaks(self):
        keyword = "是ZF-DA组，我们将不定时抽查各组的值班和远程在线情况，以确保能够及时联系到值班或远程员工。请您在看到此消息后尽快回复"
        self.reply.config = replace(self.config, all_chats=True, keywords=(keyword,))
        message = replace(self.message, chat_id=999,
                          text="您好！这里" + keyword.replace("情况，", "情况，\n") + "，谢谢配合！")
        self.assertTrue(await self.reply.handle(message))
        self.assertFalse(await self.reply.handle(replace(message, message_id=2, text="其他通知")))

    async def test_captionless_media_allowed_without_keywords(self):
        self.assertTrue(await self.reply.handle(replace(self.message, text="")))

    async def test_explicit_bot_opt_in_can_reply_to_matching_notification(self):
        self.reply.config = replace(self.config, ignore_bots=False, keywords=("查岗",))
        self.assertTrue(await self.reply.handle(replace(self.message, bot=True)))

    async def test_concurrent_events_send_once(self):
        result = await asyncio.gather(*(self.reply.handle(self.message) for _ in range(5)))
        self.assertEqual(result.count(True), 1)
        self.assertEqual(self.captured, 1)

    async def test_exact_command_including_own_sent_command(self):
        self.reply.config = replace(self.config, all_chats=True, keywords=("telcut",),
                                    trigger_mode="exact", include_outgoing=True,
                                    quote_reply=False, send_as_document=False)
        for text in ("请发送telCut", "telCut extra", "tel Cut", "", "查岗 CHECK"):
            with self.subTest(text=text):
                self.assertFalse(await self.reply.handle(replace(self.message, text=text)))
        command = replace(self.message, text=" telCut\n", outgoing=True)
        self.assertTrue(await self.reply.handle(command))
        self.assertFalse(await self.reply.handle(replace(command, message_id=2, text="")))
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
        await self.adapter_roundtrip(all_chats=False)

    async def test_all_chats_can_reply_to_a_new_dialog_not_in_startup_list(self):
        await self.adapter_roundtrip(all_chats=True)

    async def test_command_posts_photo_without_text_or_quoted_reply(self):
        await self.adapter_roundtrip(all_chats=True, command_mode=True)

    async def adapter_roundtrip(self, all_chats, command_mode=False):
        now = time.time()
        uploaded = []
        options = {}
        target_id = 999 if all_chats else 123

        class FakeClient:
            async def start(self):
                pass

            async def disconnect(self):
                options["disconnected"] = True

            async def iter_dialogs(self):
                yield SimpleNamespace(id=123, input_entity="target-peer")

            def on(self, builder):
                options["chats_filter"] = builder.chats
                options["incoming_filter"] = builder.incoming
                def register(callback):
                    self.callback = callback
                    return callback
                return register

            async def get_input_entity(self, chat_id):
                self_test.assertEqual(chat_id, target_id)
                return "new-peer"

            async def send_file(self, entity, file, **kwargs):
                uploaded.append((entity, file.read(), kwargs))

            async def run_until_disconnected(self):
                async def get_sender():
                    return SimpleNamespace(bot=False)

                event = SimpleNamespace(
                    is_channel=False, is_group=False, chat_id=target_id, id=42,
                    sender_id=456, raw_text="telCut" if command_mode else "查岗", out=command_mode,
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

        self_test = self
        with tempfile.TemporaryDirectory() as directory, \
                patch("telcut.cli.credentials", return_value=(12345, "a" * 32)), \
                patch("telcut.cli.TelegramClient", side_effect=client_factory), \
                patch("telcut.capture.sys.platform", "darwin"), \
                patch("telcut.capture.subprocess.run", side_effect=capture):
            config = Config(all_chats=all_chats, chat_ids=(123,), keywords=("查岗",))
            if command_mode:
                config = replace(config, keywords=("telcut",), trigger_mode="exact",
                                 include_outgoing=True, quote_reply=False, send_as_document=False,
                                 capture=CaptureConfig(width_pixels=868, height_pixels=62))
            await telegram_command("run", config, Path(directory))
        self.assertEqual(len(uploaded), 1)
        entity, png, kwargs = uploaded[0]
        self.assertEqual(entity, "new-peer" if all_chats else "target-peer")
        self.assertEqual(kwargs["reply_to"], None if command_mode else 42)
        self.assertEqual(kwargs["force_document"], not command_mode)
        self.assertIsNone(kwargs["caption"])
        self.assertIsNone(kwargs["parse_mode"])
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (868, 62) if command_mode else (500, 32))
        self.assertFalse(options["catch_up"])
        self.assertEqual(options["flood_sleep_threshold"], 0)
        self.assertEqual(options["chats_filter"], None if all_chats else [123])
        self.assertEqual(options["incoming_filter"], None if command_mode else True)
        self.assertTrue(options["disconnected"])
