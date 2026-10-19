"""The few herdr CLI calls the plugin makes. All of them fail quietly outside herdr."""

import json
import os
import subprocess

PLUGIN_ID = "negoziator.dev-null"


def _bin():
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def _run(*args, timeout=10):
    try:
        return subprocess.run([_bin(), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def notify(title, body=None, sound=None):
    args = ["notification", "show", title]
    if body:
        args += ["--body", body]
    if sound:
        args += ["--sound", sound]
    _run(*args)


def pane_cwd(pane_id):
    """The directory the pane's foreground process is in, which follows an agent's `cd`."""
    result = _run("pane", "get", pane_id)
    if not result or result.returncode != 0:
        return None
    try:
        pane = json.loads(result.stdout)["result"]["pane"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(pane, dict):
        return None
    cwd = pane.get("foreground_cwd") or pane.get("cwd")
    return cwd if isinstance(cwd, str) else None


def open_pane(entrypoint, *options):
    """Opens one of the plugin's panes. Only one popup can be open at a time."""
    result = _run("plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", entrypoint, *options, "--focus")
    if result is None:
        return False, "herdr is not reachable."
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or "herdr could not open the pane."
    return True, ""
