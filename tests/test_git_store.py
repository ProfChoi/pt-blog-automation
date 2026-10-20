"""Exercise real local Git remotes; no network or credentials required."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from delivery import DeliveryStore, DeliveryError


@contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(directory, *args):
    return subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


class GitCheckpointTests(unittest.TestCase):
    def test_remote_checkpoints_used_history_and_stale_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, first, second = (root / name for name in ("remote.git", "first", "second"))
            git(root, "init", "--bare", "--initial-branch=main", str(remote))
            git(root, "clone", str(remote), str(first))
            for directory in (first,):
                git(directory, "config", "user.name", "Test")
                git(directory, "config", "user.email", "test@example.org")
            (first / "used_articles.txt").write_text("https://example.org/old\n", encoding="utf-8")
            git(first, "add", "used_articles.txt")
            git(first, "commit", "-m", "Initial")
            git(first, "push", "origin", "main")
            git(root, "clone", str(remote), str(second))
            git(second, "config", "user.name", "Test")
            git(second, "config", "user.email", "test@example.org")
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "DELIVERY_BRANCH": "main"}):
                with cwd(first):
                    store = DeliveryStore()
                    row = {
                        "date": "2026-10-20", "status": "prepared",
                        "message_id": "<test@example.org>",
                        "article": {"url": "https://example.org/new", "title": "New"},
                    }
                    store.state["deliveries"][row["date"]] = row
                    store.checkpoint()
                    row["status"] = "sending"
                    store.checkpoint()
                    self.assertEqual(store.used(), {"https://example.org/old"})
                    remote_state = json.loads(git(remote, "show", "main:delivery_state.json"))
                    self.assertEqual(remote_state["deliveries"][row["date"]]["status"], "sending")
                    row["status"] = "sent"
                    store.checkpoint()
                    self.assertEqual(
                        set(git(remote, "show", "main:used_articles.txt").splitlines()),
                        {"https://example.org/old", "https://example.org/new"},
                    )
                with cwd(second):
                    stale = DeliveryStore()
                    with self.assertRaises(DeliveryError):
                        stale.checkpoint()
                remote_state = json.loads(git(remote, "show", "main:delivery_state.json"))
                self.assertEqual(remote_state["deliveries"][row["date"]]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
