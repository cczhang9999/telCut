import sqlite3
from pathlib import Path


class State:
    """Reserve each event before upload: uncertain failures never resend it."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY, last_attempt REAL NOT NULL
            );
        """)

    def claim(self, chat_id, message_id, now, cooldown):
        with self.db:
            seen = self.db.execute(
                "SELECT 1 FROM messages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
            if seen:
                return False
            last = self.db.execute("SELECT last_attempt FROM chats WHERE chat_id=?",
                                   (chat_id,)).fetchone()
            cooling = last is not None and now - last[0] < cooldown
            self.db.execute("INSERT INTO messages VALUES (?, ?, ?, ?)",
                            (chat_id, message_id, "skipped" if cooling else "pending", now))
            if cooling:
                return False
            self.db.execute("INSERT OR REPLACE INTO chats VALUES (?, ?)", (chat_id, now))
            return True

    def finish(self, chat_id, message_id, status):
        with self.db:
            self.db.execute("UPDATE messages SET status=? WHERE chat_id=? AND message_id=?",
                            (status, chat_id, message_id))

    def close(self):
        self.db.close()
