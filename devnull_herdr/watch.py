"""Tells you when someone replies, mentions, quotes or follows you.

herdr has no timers, so a startup hook starts this loop in the background. It asks /dev/null
for your unread count once a minute, which marks nothing read, and shows a notification when
the count goes up. One loop runs at a time; it ends when herdr's server does.
"""

import fcntl
import os
import socket
import subprocess
import sys
import time

from . import api, herdr, paths

EVERY = 60
NOT_SIGNED_IN_EVERY = 300


def _lock():
    """The watcher's lock, or None when another watcher holds it."""
    fd = os.open(paths.state_dir() / "watch.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def herdr_running():
    """Whether herdr's server still answers on its socket. Without one, there is no herdr."""
    path = os.environ.get("HERDR_SOCKET_PATH")
    if not path:
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(2)
        try:
            probe.connect(path)
        except OSError:
            return False
    return True


def check(last, notify=herdr.notify, client=api):
    """One look at the unread count. Returns the new count, and notifies when it went up."""
    try:
        count = int((client.me() or {}).get("unread_notifications_count") or 0)
    except (TypeError, ValueError):
        raise api.ApiError(0, "bad_answer", "/dev/null's unread count isn't a number.") from None
    if last is not None and count > last:
        more = count - last
        notify("/dev/null", f"{more} new notification{'s' if more != 1 else ''}. Open the feed (4) to see them.",
               sound="request")
    return count


def loop(sleep=time.sleep, running=herdr_running, notify=herdr.notify, client=api):
    fd = _lock()
    if fd is None:
        return 0
    last, warned = None, False
    try:
        while running():
            if not client.account():
                last = None
                sleep(NOT_SIGNED_IN_EVERY)
                continue
            try:
                last = check(last, notify, client)
                warned = False
                sleep(EVERY)
            except api.ApiError as error:
                if error.code == "device_not_approved" and not warned:
                    notify("/dev/null", str(error))
                    warned = True
                sleep(max(error.retry_after or 0, EVERY * (5 if error.status == 401 else 1)))
            except Exception as error:  # noqa: BLE001 - one odd answer must not end the watcher
                print(f"devnull watch: {type(error).__name__}: {error}", file=sys.stderr)
                sleep(EVERY)
    finally:
        os.close(fd)
    return 0


def detach():
    """Starts the loop in the background and returns at once: what the startup hook runs."""
    log = open(paths.state_dir() / "watch.log", "ab")
    subprocess.Popen([sys.executable, str(paths.ROOT / "bin" / "devnull"), "watch"],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return 0
