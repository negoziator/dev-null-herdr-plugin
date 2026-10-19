"""Signing in to /dev/null as a device, once.

1. The plugin makes its own ed25519 key, in its config directory, used for nothing else.
2. Your browser opens /dev/null's approval page for that key; you press Approve.
3. /dev/null sends the browser to a devnull:// link carrying a one-time code. For the few
   minutes of the sign-in, the plugin is the desktop's handler for devnull:// links (Linux).
   Whatever was the handler before is restored afterwards, however the sign-in ends.
   The browser runs `bin/devnull handle-url <link>`, which passes the link to the waiting
   sign-in over a private socket. Pasting the link works too, and is the only way on macOS.
4. The code is redeemed with the key, which then signs the feed's requests.

You can sign the device out at any time on /dev/null, under Settings → Devices.
"""

import base64
import hashlib
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

from . import api, herdr, paths
from .text import clean

REDIRECT_URI = "devnull://auth/callback"
SCHEME = "x-scheme-handler/devnull"
DESKTOP = "herdr-devnull.desktop"
TIMEOUT = 300  # the code /dev/null hands out lives five minutes


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce():
    verifier = _b64url(os.urandom(32))
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def fingerprint(public_key):
    """SHA256:… of an OpenSSH public key line, as `ssh-keygen -l` prints it."""
    blob = base64.b64decode(public_key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).rstrip(b"=").decode()


def ensure_key():
    """The plugin's device key, made the first time. Unencrypted, so the feed can sign by
    itself: it lives only in the plugin's config directory, readable by you alone."""
    key = api.key_path()
    if not key.is_file():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "herdr dev-null plugin", "-f", str(key)],
                       check=True, timeout=30)
    os.chmod(key, 0o600)
    return key, Path(f"{key}.pub").read_text().strip()


def authorize_url(public_key, challenge, state, device_name):
    return f"{api.BASE}/mobile/authorize?" + urllib.parse.urlencode({
        "fingerprint": fingerprint(public_key),
        "device_name": device_name[:100],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "redirect_uri": REDIRECT_URI,
    })


def code_from(link, state):
    """The code in a callback link, None when the link is not this sign-in's, or raises when
    the approval was refused."""
    parsed = urllib.parse.urlsplit(link.strip())
    if parsed.scheme != "devnull":
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("state", [None])[0] != state:
        return None  # a stray or old link: keep waiting for the right one
    if query.get("error"):
        raise RuntimeError("The sign-in was refused on /dev/null." if query["error"][0] == "access_denied"
                           else f"/dev/null refused the sign-in: {query['error'][0]}")
    code = query.get("code", [""])[0]
    return code or None


def socket_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or paths.state_dir()
    return Path(base) / f"devnull-herdr-{os.getuid()}.sock"


# The devnull:// handler, registered only while a sign-in waits.

class Handler:
    """Makes the plugin the devnull:// handler, and puts back the previous one on exit."""

    def __init__(self, run=subprocess.run):
        self.run = run
        self.applications = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
        self.desktop = self.applications / DESKTOP
        self.previous = None
        self.active = False

    def _xdg(self, *args):
        try:
            result = self.run(["xdg-mime", *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def _saved(self):
        return paths.state_dir() / "handler-previous.json"

    def __enter__(self):
        if not sys.platform.startswith("linux") or not shutil.which("xdg-mime"):
            return self  # macOS and friends: pasting the link is the way
        self.previous = self._xdg("query", "default", SCHEME) or None
        if self.previous == DESKTOP:
            # Left over from a sign-in that was killed: what was there before it is saved.
            self.previous = paths.read_json(self._saved(), {}).get("previous")
        else:
            paths.write_json(self._saved(), {"previous": self.previous})
        self.applications.mkdir(parents=True, exist_ok=True)
        root = paths.ROOT
        self.desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=/dev/null sign-in for herdr\n"
            "Comment=Hands a /dev/null sign-in back to the herdr plugin\n"
            f"Exec={exec_argument(sys.executable)} {exec_argument(root / 'bin' / 'devnull')} handle-url %u\n"
            "Terminal=false\n"
            "NoDisplay=true\n"
            f"MimeType={SCHEME};\n"
        )
        self._xdg("default", DESKTOP, SCHEME)
        self._refresh()
        self.active = True
        return self

    def __exit__(self, *exc):
        if not self.active:
            return False
        if self.previous:
            self._xdg("default", self.previous, SCHEME)
        else:
            _forget_default(SCHEME, DESKTOP)
        self.desktop.unlink(missing_ok=True)
        self._saved().unlink(missing_ok=True)
        self._refresh()
        return False

    def _refresh(self):
        if shutil.which("update-desktop-database"):
            try:
                self.run(["update-desktop-database", str(self.applications)], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass


def exec_argument(path):
    """A path quoted for a desktop entry's Exec line, as the Desktop Entry spec says."""
    text = str(path).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        text = text.replace(char, "\\" + char)
    return f'"{text}"'


def _forget_default(scheme, desktop):
    """Takes our entry out of mimeapps.list when no handler was set before: xdg-mime can set a
    default but not unset one. Other handlers on the same line stay."""
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mimeapps.list"
    try:
        lines = path.read_text().splitlines(keepends=True)
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    kept = []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == scheme:
            others = [entry for entry in value.strip().split(";") if entry and entry != desktop]
            if not others:
                continue
            line = f"{key}={';'.join(others)};\n"
        kept.append(line)
    if kept != lines:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text("".join(kept))
        os.chmod(tmp, mode)
        os.replace(tmp, path)


# Waiting for the link.

def wait_for_code(state, sock, stdin=sys.stdin, timeout=TIMEOUT, clock=time.monotonic):
    """The code, from the browser's handler over `sock` or pasted on stdin."""
    deadline = clock() + timeout
    sources = [sock] + ([stdin] if stdin is not None and stdin.isatty() else [])
    while clock() < deadline:
        ready, _, _ = select.select(sources, [], [], min(1.0, max(deadline - clock(), 0)))
        for source in ready:
            if source is sock:
                connection, _ = sock.accept()
                with connection:
                    connection.settimeout(5)
                    link = connection.recv(8192).decode(errors="replace")
            else:
                link = source.readline()
            code = code_from(link, state)
            if code:
                return code
            if link.strip():
                print("That link is not from this sign-in. Waiting for the right one…")
    raise RuntimeError("The sign-in timed out: nothing came back from /dev/null within five minutes.")


def listen():
    path = socket_path()
    path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    sock.listen(4)
    return sock, path


def handle_url(link):
    """Run by the browser for a devnull:// link: hands it to the sign-in that waits for it."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(socket_path()))
            sock.sendall(link.encode())
    except OSError:
        herdr.notify("/dev/null", "No sign-in is waiting for this link: start “Sign in to /dev/null” again.")
        return 1
    return 0


def open_browser(url):
    for opener in (["xdg-open"], ["open"]):
        if shutil.which(opener[0]):
            try:
                subprocess.Popen([*opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return True
            except OSError:
                pass
    return webbrowser.open(url)


def run(device_name=None, handler=Handler, browser=open_browser, redeem=None):
    """The whole sign-in. Returns the account it signed in as."""
    key, public_key = ensure_key()
    verifier, challenge = pkce()
    state = os.urandom(16).hex()
    url = authorize_url(public_key, challenge, state, device_name or f"herdr on {socket.gethostname()}")

    # Closing the pane or herdr sends a signal: end like Ctrl-C, so the handler is put back.
    previous_signals = {number: signal.signal(number, _interrupt) for number in (signal.SIGHUP, signal.SIGTERM)}
    sock, path = listen()
    try:
        with handler():
            print("Approve this device on /dev/null in the browser that opens now.")
            print("If no browser opens, visit:\n\n  " + url + "\n")
            print("After Approve, this finishes by itself. If it doesn't, paste the devnull:// link here.\n")
            browser(url)
            code = wait_for_code(state, sock)
    finally:
        sock.close()
        path.unlink(missing_ok=True)
        for number, previous in previous_signals.items():
            signal.signal(number, previous)

    answer = (redeem or _redeem)(code, verifier, key)
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, dict) or not data.get("username"):
        raise RuntimeError("/dev/null approved the sign-in but its answer had no account in it.")
    signed_in = {"username": clean(str(data["username"]))[:64], "name": clean(str(data.get("name") or ""))[:100],
                 "signed_in_at": int(time.time())}
    paths.write_json(api.account_path(), signed_in)
    return signed_in


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _redeem(code, verifier, key):
    return api.request("POST", "/api/v1/devices", body={"code": code, "code_verifier": verifier}, key=key)


def logout():
    """Forgets the account here. The device stays approved on /dev/null until you remove it
    under Settings → Devices; the key is kept so that signing in again reuses it."""
    api.account_path().unlink(missing_ok=True)


def main(args):
    try:
        if getattr(args, "logout", False):
            logout()
            print("Signed out here. To revoke the device too: /dev/null → Settings → Devices.")
            return 0
        current = api.account()
        if current and not getattr(args, "again", False):
            print(f"Already signed in as @{current['username']}. `bin/devnull login --again` signs in anew.")
            return 0
        account = run()
        print(f"\nSigned in as @{account['username']}. The feed is ready.")
        herdr.notify("/dev/null", f"Signed in as @{account['username']}.", sound="done")
        return 0
    except (RuntimeError, api.ApiError, OSError, subprocess.SubprocessError) as error:
        print(f"\nNot signed in: {error}")
        return 1
    except KeyboardInterrupt:
        print("\nSign-in cancelled.")
        return 130

