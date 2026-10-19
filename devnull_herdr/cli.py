import argparse
import json
import os
import sys

from . import __version__, compose, drafts, herdr, paths, script


def _commit_options(parser):
    parser.add_argument("--reply", metavar="HASH", help="reply to a commit (its hash or link)")
    parser.add_argument("--image", action="append", metavar="PATH", help="attach an image, up to 3 times")
    parser.add_argument("--dry-run", action="store_true", help="show what would be committed, send nothing")


def parser():
    root = argparse.ArgumentParser(prog="devnull", description="Commit to /dev/null from herdr.")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    edit = commands.add_parser("compose", help="write a commit in your editor, review it, commit it")
    edit.add_argument("text", nargs="?", help="text to start from")
    edit.add_argument("--draft", action="store_true", help="start from the newest draft")
    _commit_options(edit)

    post = commands.add_parser("post", help="commit a message without the editor")
    post.add_argument("text")
    _commit_options(post)

    commands.add_parser("hook", help="handle a herdr event (run by herdr)")

    open_ = commands.add_parser("open", help="open one of the plugin's popups in herdr")
    open_.add_argument("entrypoint", choices=["compose", "review-draft"])

    toggle = commands.add_parser("drafts", help="list drafts, or turn drafting on or off")
    toggle.add_argument("state", nargs="?", choices=["on", "off", "toggle", "list"], default="list")

    check = commands.add_parser("check-script", help="compare the commit script with the live one")
    check.add_argument("--reset", action="store_true", help="drop an accepted update, use the vendored copy")
    return root


def _post(args):
    text = compose.message(args.text)
    error = compose.problem(text)
    if error:
        print(error, file=sys.stderr)
        return 1
    return compose.commit(text, args.reply, args.image or [], None, args.dry_run)


def _hook():
    """Never fails: a broken event is logged in one line and herdr carries on."""
    try:
        event = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
        drafts.handle(event)
    except Exception as error:  # noqa: BLE001 - a hook must not crash on odd input
        print(f"devnull: hook skipped an event: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def _open(args):
    ok, error = herdr.open_pane(args.entrypoint)
    if not ok:
        print(error, file=sys.stderr)
        herdr.notify("/dev/null", error)
    return 0 if ok else 1


def _drafts(args):
    if args.state == "list":
        for path in sorted(drafts.drafts_dir().glob("*.json")):
            draft = paths.read_json(path, {})
            if not isinstance(draft.get("subjects"), list):
                continue
            print(f"{draft.get('name')}: {len(draft.get('subjects', []))} commits ({path})")
        print(f"Drafting is {'on' if paths.config()['drafts'] else 'off'}.")
        return 0
    enabled = {"on": True, "off": False, "toggle": not paths.config()["drafts"]}[args.state]
    paths.save_config({"drafts": enabled})
    status = f"Drafts are {'on' if enabled else 'off'}."
    print(status)
    herdr.notify("/dev/null", status)
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "compose":
        return compose.main(args)
    if args.command == "post":
        return _post(args)
    if args.command == "hook":
        return _hook()
    if args.command == "open":
        return _open(args)
    if args.command == "drafts":
        return _drafts(args)
    if args.command == "check-script":
        if args.reset:
            script.reset()
        script.check_for_update()
        print(f"Committing with {script.current()}")
        return 0
    return 2
