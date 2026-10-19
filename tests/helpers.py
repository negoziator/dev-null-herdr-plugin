import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


class Isolated(unittest.TestCase):
    """Points the plugin's state and config at a temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._env = dict(os.environ)
        os.environ["HERDR_PLUGIN_STATE_DIR"] = str(self.tmp / "state")
        os.environ["HERDR_PLUGIN_CONFIG_DIR"] = str(self.tmp / "config")
        os.environ.pop("HERDR_PLUGIN_ENTRYPOINT_ID", None)
        # No test may reach a real herdr.
        os.environ["HERDR_BIN_PATH"] = "/nonexistent/herdr"
        # Nor the network: fetch errors are swallowed, so a call is recorded and fails the test.
        self.network_calls = []
        refuse = lambda *a, **k: self.network_calls.append(a) or 1 / 0  # noqa: E731
        for target in ("urllib.request.urlopen", "devnull_herdr.api._OPENER.open"):
            patcher = mock.patch(target, side_effect=refuse)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertEqual(self.network_calls, [], "a test reached the network"))
        # Nor wait for a keyboard: a prompt a test did not answer fails it at once.
        patcher = mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def git_repo(self, name="project"):
        repo = self.tmp / name
        repo.mkdir()
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "commit.gpgsign", "false")
        self.commit(repo, "Initial commit")
        return repo

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, repo, subject, filename=None):
        path = Path(repo) / (filename or f"{len(list(Path(repo).iterdir()))}.txt")
        path.write_text(subject + "\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", subject)
