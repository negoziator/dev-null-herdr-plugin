"""Where the plugin keeps its files.

herdr hands every command its own config and state directories. Outside herdr (running
`bin/devnull` from a shell) the XDG equivalents are used, so both see the same files.
Nothing is ever written to the plugin directory: a GitHub install is a managed checkout.
The sign-in key and unsent posts are yours alone, so every file is private to the user.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


PLUGIN_ID = "negoziator.dev-null"


def _dir(env, xdg_env, xdg_default, herdr_dir):
    """herdr's directory for the plugin; from a shell the same one when herdr made it, so that
    a sign-in is the same in both; else an XDG one."""
    if os.environ.get(env):
        path = Path(os.environ[env])
    else:
        base = Path(os.environ.get(xdg_env) or Path.home() / xdg_default)
        herdr_path = base / herdr_dir / PLUGIN_ID
        path = herdr_path if herdr_path.is_dir() else base / "dev-null-herdr"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def state_dir():
    return _dir("HERDR_PLUGIN_STATE_DIR", "XDG_STATE_HOME", ".local/state", "herdr/plugins")


def config_dir():
    return _dir("HERDR_PLUGIN_CONFIG_DIR", "XDG_CONFIG_HOME", ".config", "herdr/plugins/config")


def private_file(path, data, mode="w"):
    """Writes a file only the user can read, atomically."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, mode) as handle:
        handle.write(data)
    os.replace(tmp, path)


def read_json(path, default):
    """The file's JSON, or default when it is missing, unreadable or not the same type."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default
    return data if default is None or isinstance(data, type(default)) else default


def write_json(path, data):
    private_file(path, json.dumps(data, indent=2) + "\n")


def config():
    values = {"dry_run": False, **read_json(config_dir() / "config.json", {})}
    values.pop("drafts", None)  # the setting of a feature that is gone
    values["dry_run"] = values["dry_run"] is True
    return values


def save_config(values):
    write_json(config_dir() / "config.json", {**config(), **values})
