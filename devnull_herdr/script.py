"""The official commit script, pinned.

Commits go through /dev/null's own script (https://dev-null.sh/commit), never piped from the
network: the plugin ships a reviewed copy in vendor/, and before each commit compares it with
the live one. A new version is only used once you have chosen to update to it, and is then kept
in the state directory, until a plugin release ships a newer reviewed copy.
"""

import difflib
import hashlib
import os
import re
import shutil
import subprocess
import urllib.request

from . import paths
from .text import visible

REMOTE_URL = "https://dev-null.sh/commit"
VENDORED = paths.ROOT / "vendor" / "commit.sh"
COMMITTED = re.compile(r"Committed: (https://\S+)")
MAX_SIZE = 1_000_000


def installed():
    return paths.state_dir() / "commit.sh"


def _installed_for():
    return paths.state_dir() / "commit.sh.vendored-sha256"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def current():
    """The script commits run with: an accepted update, or the vendored copy.

    An update was accepted over one vendored copy. When the plugin ships another, that newer
    reviewed copy wins and the update is dropped.
    """
    path = installed()
    if path.is_file():
        try:
            accepted_over = _installed_for().read_text().strip()
        except OSError:
            accepted_over = None
        if accepted_over == sha256(VENDORED.read_bytes()):
            return path
        reset()
    return VENDORED


def reset():
    for path in (installed(), _installed_for()):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def fetch_remote(timeout=10):
    """The live script, or None when /dev/null cannot be reached or answers something odd."""
    request = urllib.request.Request(REMOTE_URL, headers={"User-Agent": "dev-null-herdr-plugin"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_SIZE + 1)
    except Exception:  # network errors are not all OSError (http.client.IncompleteRead)
        return None
    return data if looks_like_script(data) else None


def looks_like_script(data):
    """Not a proof of anything: keeps an error or captive-portal page from being offered."""
    return len(data) <= MAX_SIZE and data.startswith(b"#!") and b"\0" not in data


def diff(local, remote):
    return visible(
        "".join(
            difflib.unified_diff(
                local.decode(errors="replace").splitlines(keepends=True),
                remote.decode(errors="replace").splitlines(keepends=True),
                "current/commit.sh",
                "dev-null.sh/commit",
            )
        )
    )


def install(remote):
    paths.private_file(installed(), remote, mode="wb")
    paths.private_file(_installed_for(), sha256(VENDORED.read_bytes()) + "\n")


def _page(text):
    # Without -R: what you are about to trust is shown as it is, escape sequences included.
    pager = shutil.which("less")
    if pager:
        subprocess.run([pager], input=text, text=True)
    else:
        print(text)


def check_for_update(ask=None, fetch=None, show=None, out=print):
    """Asks what to do when the live script differs from the current one.

    Returns once the current script is the one to commit with: unchanged, updated, or the
    new version discarded. Discarding is not remembered, so you are asked again next time.
    """
    ask = ask or input
    remote = (fetch or fetch_remote)()
    if remote is None:
        out("Could not check for a new version of the commit script, using the current one.")
        return
    local = current().read_bytes()
    if sha256(local) == sha256(remote):
        return
    while True:
        out("New version of /dev/null available. 1) See diff 2) Update 3) Discard")
        choice = ask("> ").strip()
        if choice == "1":
            (show or _page)(diff(local, remote))
        elif choice == "2":
            install(remote)
            out("Updated.")
            return
        elif choice == "3":
            out("Keeping the current version.")
            return
        else:
            out("Pick 1, 2 or 3.")


def command(message, reply=None, images=()):
    args = ["bash", str(current())]
    if reply:
        args += ["--reply", reply]
    for image in images:
        args += ["--image", image]
    return args + ["--", message]


def commit(message, reply=None, images=()):
    """Runs the script. Its errors and any passphrase prompt reach the terminal directly.

    Returns (ok, url, output); url is None when the answer had no commit link.
    """
    try:
        result = subprocess.run(command(message, reply, images), stdout=subprocess.PIPE, text=True)
    except OSError as error:
        return False, None, f"Could not run the commit script: {error}"
    match = COMMITTED.search(result.stdout)
    return result.returncode == 0, match.group(1) if match else None, visible(result.stdout.strip())
