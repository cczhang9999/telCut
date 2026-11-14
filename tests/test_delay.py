import asyncio
import unittest
from dataclasses import replace
from pathlib import Path

from telcut.config import Config
from telcut.service import AutoReply, Incoming
from telcut.state import State


class DelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = State(Path(":memory:"))
        self.now = 1000.0
        self.captures = []
        self.sent = []
        self.config = Config(all_chats=True, keywords=("是ZF-DA组",), cooldown_seconds=0,
                             delay_min_seconds=30, delay_max_seconds=120)
        self.message = Incoming(123, 1, 456, "您好！这里是ZF-DA组，我们将不定时抽查。", 1000)

    def tearDown(self):
        self.state.close()

    def responder(self, sleep, choose=lambda minimum, maximum: 60):
        def capture(config):
            self.captures.append(self.now)
            return b"png"

        async def send(message, file, caption):
            self.sent.append((message.message_id, self.now, file.read()))

        return AutoReply(self.config, self.state, send, capture=capture,
                         clock=lambda: self.now, sleep=sleep, choose_delay=choose)

    async def test_boundary_delays_capture_after_wait_and_do_not_expire_accepted_message(self):
        for delay in (30, 120):
            with self.subTest(delay=delay):
                self.now = 1000
                count = len(self.captures)
                chosen_ranges = []

                def choose(minimum, maximum):
                    chosen_ranges.append((minimum, maximum))
                    return delay

                async def sleep(seconds):
                    self.assertEqual(seconds, delay)
                    self.assertEqual(len(self.captures), count)
                    self.now += seconds

                reply = self.responder(sleep, choose)
                # Fresh at admission (age 115), even though the intentional
                # delay takes it beyond the 120-second admission age limit.
                message = replace(self.message, message_id=delay, timestamp=885)
                self.assertTrue(await reply.handle(message))
                self.assertEqual(chosen_ranges, [(30, 120)])
                self.assertEqual(self.captures[-1], 1000 + delay)
                self.assertEqual(self.sent[-1], (delay, 1000 + delay, b"png"))

    async def test_chats_wait_independently(self):
        waiting = []
        both_waiting = asyncio.Event()
        release = asyncio.Event()

        async def sleep(seconds):
            waiting.append(seconds)
            if len(waiting) == 2:
                both_waiting.set()
            await release.wait()

        reply = self.responder(sleep)
        tasks = [asyncio.create_task(reply.handle(message)) for message in (
            self.message, replace(self.message, chat_id=789, message_id=2))]
        try:
            await asyncio.wait_for(both_waiting.wait(), timeout=1)
            self.assertEqual(self.captures, [])
            self.assertEqual(waiting, [60, 60])
        finally:
            release.set()
            results = await asyncio.gather(*tasks)
        self.assertEqual(results, [True, True])
        self.assertEqual(len(self.sent), 2)

    async def test_duplicate_during_delay_does_not_schedule_or_send_again(self):
        waiting = asyncio.Event()
        release = asyncio.Event()
        sleeps = []

        async def sleep(seconds):
            sleeps.append(seconds)
            waiting.set()
            await release.wait()

        reply = self.responder(sleep)
        task = asyncio.create_task(reply.handle(self.message))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=1)
            self.assertFalse(await reply.handle(self.message))
            self.assertEqual(self.captures, [])
        finally:
            release.set()
            await task
        self.assertEqual(sleeps, [60])
        self.assertEqual(len(self.sent), 1)

    async def test_stopping_cancels_pending_capture_without_sending(self):
        waiting = asyncio.Event()

        async def sleep(seconds):
            waiting.set()
            await asyncio.Event().wait()

        reply = self.responder(sleep)
        task = asyncio.create_task(reply.handle(self.message))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.captures, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.db.execute("SELECT status FROM messages").fetchone(),
                         ("cancelled",))

    async def test_rate_limit_during_delay_does_not_send_a_queued_image(self):
        async def sleep(seconds):
            reply.blocked_until = self.now + 60

        reply = self.responder(sleep)
        self.assertFalse(await reply.handle(self.message))
        self.assertEqual(self.captures, [])
        self.assertEqual(self.sent, [])

