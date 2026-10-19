"""Signed requests to /dev/null's API, as the device the plugin signed in as.

Each request is signed with the plugin's own device key, in the SSHSIG format of
`ssh-keygen -Y sign`, over its timestamp, nonce, method, URI and body.

A device key can do whatever the account can, deleting the account included. So the client
sends only the requests on ALLOWED, and refuses anything else before it is signed.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import paths
from .text import clean

BASE = "https://dev-null.sh"
NAMESPACE = "devnull-api"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ALLOWED = [
    ("GET", re.compile(r"/api/v1/me")),
    ("GET", re.compile(r"/api/v1/log/(following|latest|trending)")),
    ("GET", re.compile(r"/api/v1/notifications")),
    ("GET", re.compile(rf"/api/v1/commits/{UUID}/replies")),
    ("POST", re.compile(rf"/api/v1/commits/{UUID}/star")),
    ("DELETE", re.compile(rf"/api/v1/commits/{UUID}/star")),
    ("POST", re.compile(r"/api/v1/devices")),  # redeeming a sign-in, once
]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the signature headers to wherever it points: never follow one."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


class ApiError(Exception):
    def __init__(self, status, code, message, retry_after=None):
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


class NotSignedIn(ApiError):
    def __init__(self, message="Not signed in: run “Sign in to /dev/null” first."):
        super().__init__(401, "not_signed_in", message)


def account_path():
    return paths.config_dir() / "account.json"


def key_path():
    return paths.config_dir() / "device_ed25519"


def account():
    """The signed-in account ({"username", ...}), or None."""
    data = paths.read_json(account_path(), {})
    return data if data.get("username") and key_path().is_file() else None


def allowed(method, path):
    return any(method == verb and pattern.fullmatch(path) for verb, pattern in ALLOWED)


def uri(path, params=None):
    """The request URI exactly as it is sent, and so as it is signed."""
    params = {key: value for key, value in (params or {}).items() if value is not None}
    return path + ("?" + urllib.parse.urlencode(params) if params else "")


def message(timestamp, nonce, method, request_uri, body):
    return "\n".join([str(timestamp), nonce, method, request_uri, body]).encode()


def sign(data, key=None):
    """The SSHSIG signature of `data` as one line, for the X-SSH-Signature header."""
    result = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-n", NAMESPACE, "-f", str(key or key_path())],
        input=data, capture_output=True, timeout=10,
    )
    if result.returncode != 0:
        raise ApiError(0, "signing_failed", result.stderr.decode(errors="replace").strip() or "ssh-keygen failed")
    lines = result.stdout.decode().splitlines()
    return "".join(line.strip() for line in lines if line.strip() and "-----" not in line)


def request(method, path, params=None, body=None, key=None, opener=None, now=None):
    """Sends one signed request. Returns the decoded JSON answer; raises ApiError otherwise."""
    if not allowed(method, path):
        raise ApiError(0, "not_allowed", f"The plugin never sends {method} {path}.")
    if key is None and account() is None:
        raise NotSignedIn()
    # Looked up when called, not when defined, so that tests can stand in for them.
    opener, now = opener or _OPENER.open, now or time.time
    request_uri = uri(path, params)
    raw = json.dumps(body) if body is not None else ""
    timestamp, nonce = int(now()), os.urandom(16).hex()
    headers = {
        "Accept": "application/json",
        "User-Agent": "dev-null-herdr-plugin",
        "X-Devnull-Timestamp": str(timestamp),
        "X-Devnull-Nonce": nonce,
        "X-SSH-Signature": sign(message(timestamp, nonce, method, request_uri, raw), key),
    }
    if raw:
        headers["Content-Type"] = "application/json"
    http = urllib.request.Request(BASE + request_uri, data=raw.encode() or None, headers=headers, method=method)
    try:
        with opener(http, timeout=15) as response:
            text = response.read(5_000_000).decode()
    except urllib.error.HTTPError as error:
        raise _error(error) from None
    except OSError as error:
        raise ApiError(0, "unreachable", f"/dev/null is not reachable: {error}") from None
    if not text.strip():
        return {}
    try:
        answer = json.loads(text)
    except ValueError:
        raise ApiError(0, "bad_answer", "/dev/null answered something that isn't JSON.") from None
    if not isinstance(answer, dict):
        raise ApiError(0, "bad_answer", "/dev/null answered something unexpected.")
    return answer


def _error(error):
    try:
        data = json.loads(error.read(100_000).decode())
    except (OSError, ValueError):
        data = {}
    retry = error.headers.get("Retry-After") if error.headers else None
    message = data.get("message") if isinstance(data, dict) else None
    code = data.get("code") if isinstance(data, dict) else None
    if code == "device_not_approved":
        message = "This device was signed out on /dev/null: sign in again."
    message = clean(str(message))[:300] if message else None
    if 300 <= error.code < 400:
        message = f"/dev/null redirected the request (HTTP {error.code}); not following it."
    return ApiError(error.code, code or f"http_{error.code}", message or f"/dev/null answered HTTP {error.code}.",
                    int(retry) if retry and retry.isdigit() else None)


# The calls the plugin makes.

def me():
    return request("GET", "/api/v1/me")


def log(name, cursor=None, since=None):
    return request("GET", f"/api/v1/log/{name}", {"cursor": cursor, "since": since})


def notifications(cursor=None):
    """Listing notifications marks them all read on /dev/null: only call it when they are shown."""
    return request("GET", "/api/v1/notifications", {"cursor": cursor})


def replies(commit_id, cursor=None):
    return request("GET", f"/api/v1/commits/{commit_id}/replies", {"cursor": cursor})


def star(commit_id, on=True):
    return request("POST" if on else "DELETE", f"/api/v1/commits/{commit_id}/star")
