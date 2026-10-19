import base64
import hashlib
import os
import subprocess
import threading
import urllib.parse
from unittest import mock

from devnull_herdr import api, login
from tests.helpers import Isolated


class PartsTest(Isolated):
    def test_pkce(self):
        verifier, challenge = login.pkce()
        self.assertRegex(verifier, r"^[A-Za-z0-9_-]{43}$")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, digest)

    def test_fingerprint_matches_ssh_keygen(self):
        key, public = login.ensure_key()
        printed = subprocess.run(["ssh-keygen", "-l", "-f", f"{key}.pub"], capture_output=True, text=True).stdout.split()[1]
        self.assertEqual(login.fingerprint(public), printed)
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o600")

    def test_authorize_url(self):
        _, public = login.ensure_key()
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(login.authorize_url(public, "c" * 43, "s", "herdr on box")).query)
        self.assertEqual(query["redirect_uri"], ["devnull://auth/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertRegex(query["fingerprint"][0], r"^SHA256:[A-Za-z0-9+/]{43}$")

    def test_code_from(self):
        self.assertEqual(login.code_from("devnull://auth/callback?code=abc&state=s1\n", "s1"), "abc")
        self.assertIsNone(login.code_from("devnull://auth/callback?code=abc&state=other", "s1"))
        self.assertIsNone(login.code_from("https://evil.example/?code=abc&state=s1", "s1"))
        with self.assertRaisesRegex(RuntimeError, "refused"):
            login.code_from("devnull://auth/callback?error=access_denied&state=s1", "s1")


class HandlerTest(Isolated):
    def setUp(self):
        super().setUp()
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdgconfig")
        self.calls, self.default = [], "dn.desktop"

    def run_(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["xdg-mime", "query", "default"]:
            return subprocess.CompletedProcess(args, 0, self.default or "", "")
        if args[:2] == ["xdg-mime", "default"]:
            self.default = args[2]
        return subprocess.CompletedProcess(args, 0, "", "")

    def handler(self):
        handler = login.Handler(run=self.run_)
        return mock.patch("sys.platform", "linux"), mock.patch("shutil.which", return_value="/usr/bin/x"), handler

    def test_takes_over_and_restores_the_previous_handler(self):
        platform, which, handler = self.handler()
        with platform, which:
            with handler:
                self.assertEqual(self.default, login.DESKTOP)
                desktop = handler.desktop.read_text()
                self.assertIn("MimeType=x-scheme-handler/devnull;", desktop)
                self.assertIn("handle-url %u", desktop)
        self.assertEqual(self.default, "dn.desktop")
        self.assertFalse(handler.desktop.exists())

    def test_restores_even_when_the_sign_in_fails(self):
        platform, which, handler = self.handler()
        with platform, which, self.assertRaises(ValueError):
            with handler:
                raise ValueError("boom")
        self.assertEqual(self.default, "dn.desktop")

    def test_no_previous_handler_leaves_none(self):
        self.default = None
        mimeapps = self.tmp / "xdgconfig" / "mimeapps.list"
        mimeapps.parent.mkdir(parents=True)
        mimeapps.write_text("[Default Applications]\ntext/html=firefox.desktop\n")
        platform, which, handler = self.handler()
        with platform, which, handler:
            mimeapps.write_text(mimeapps.read_text() + f"x-scheme-handler/devnull={login.DESKTOP}\n")
        self.assertEqual(mimeapps.read_text(), "[Default Applications]\ntext/html=firefox.desktop\n")

    def test_a_killed_sign_in_still_restores_the_real_previous_handler(self):
        platform, which, handler = self.handler()
        with platform, which:
            handler.__enter__()  # killed here: no __exit__
            self.assertEqual(self.default, login.DESKTOP)
            with login.Handler(run=self.run_):
                pass
        self.assertEqual(self.default, "dn.desktop")

    def test_other_handlers_on_the_line_stay(self):
        mimeapps = self.tmp / "xdgconfig" / "mimeapps.list"
        mimeapps.parent.mkdir(parents=True)
        mimeapps.write_text(f"[Added Associations]\nx-scheme-handler/devnull={login.DESKTOP};other.desktop;\n")
        os.chmod(mimeapps, 0o640)
        login._forget_default(login.SCHEME, login.DESKTOP)
        self.assertEqual(mimeapps.read_text(), "[Added Associations]\nx-scheme-handler/devnull=other.desktop;\n")
        self.assertEqual(oct(os.stat(mimeapps).st_mode & 0o777), "0o640")

    def test_exec_arguments_are_escaped(self):
        self.assertEqual(login.exec_argument('/a b/$x"`\\100%'), '"/a b/\\$x\\"\\`\\\\100%%"')

    def test_nothing_is_changed_outside_linux(self):
        with mock.patch("sys.platform", "darwin"), login.Handler(run=self.run_):
            pass
        self.assertEqual(self.calls, [])


class WaitTest(Isolated):
    def setUp(self):
        super().setUp()
        os.environ["XDG_RUNTIME_DIR"] = str(self.tmp)

    def test_the_browser_hands_the_link_over(self):
        sock, path = login.listen()
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        sender = threading.Thread(target=lambda: [
            login.handle_url("devnull://auth/callback?code=wrong&state=other"),
            login.handle_url("devnull://auth/callback?code=right&state=s1"),
        ])
        sender.start()
        try:
            self.assertEqual(login.wait_for_code("s1", sock, stdin=None, timeout=10), "right")
        finally:
            sender.join()
            sock.close()

    def test_timeout(self):
        sock, _ = login.listen()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            login.wait_for_code("s1", sock, stdin=None, timeout=0.2)
        sock.close()

    def test_no_sign_in_waiting(self):
        with mock.patch.object(login.herdr, "notify") as notify:
            self.assertEqual(login.handle_url("devnull://auth/callback?code=x&state=y"), 1)
        notify.assert_called_once()


class RunTest(Isolated):
    def test_a_whole_sign_in(self):
        os.environ["XDG_RUNTIME_DIR"] = str(self.tmp)
        seen = {}

        def browser(url):
            seen["state"] = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
            threading.Thread(target=login.handle_url, args=(f"devnull://auth/callback?code=c0de&state={seen['state']}",)).start()
            return True

        def redeem(code, verifier, key):
            seen["code"] = code
            return {"data": {"username": "negoziator", "name": "Lars"}}

        class NoHandler:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        account = login.run(device_name="test", handler=NoHandler, browser=browser, redeem=redeem)
        self.assertEqual((account["username"], seen["code"]), ("negoziator", "c0de"))
        self.assertEqual(api.account()["username"], "negoziator")
        self.assertFalse(login.socket_path().exists())
        login.logout()
        self.assertIsNone(api.account())
        self.assertTrue(api.key_path().exists())


