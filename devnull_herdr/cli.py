import argparse
import sys

from . import __version__, compose, feed, herdr, login, paths, script, watch


def _post_options(parser):
    parser.add_argument("--reply", metavar="HASH", help="reply to a post (its hash or link)")
    parser.add_argument("--image", action="append", metavar="PATH", help="attach an image, up to 3 times")
    parser.add_argument("--dry-run", action="store_true", help="show what would be posted, send nothing")


def parser():
    root = argparse.ArgumentParser(prog="devnull", description="Post to /dev/null from herdr.")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    edit = commands.add_parser("compose", help="write a post in a text box and post it")
    edit.add_argument("text", nargs="?", help="text to start from")
    edit.add_argument("--pending-reply", action="store_true", help="reply to the post picked in the feed")
    _post_options(edit)

    post = commands.add_parser("post", help="post without the text box")
    post.add_argument("text")
    _post_options(post)

    open_ = commands.add_parser("open", help="open one of the plugin's popups in herdr")
    open_.add_argument("entrypoint", choices=["compose", "feed"])

    commands.add_parser("feed", help="show the feed in this terminal")

    sign_in = commands.add_parser("login", help="sign in to /dev/null as a device, for the feed")
    sign_in.add_argument("--again", action="store_true", help="sign in again even when signed in")
    sign_in.add_argument("--logout", action="store_true", help="forget the sign-in here")

    handle = commands.add_parser("handle-url", help="take a devnull:// sign-in link (run by the browser)")
    handle.add_argument("url")

    watcher = commands.add_parser("watch", help="notify about new notifications (run by herdr)")
    watcher.add_argument("--detach", action="store_true", help="start in the background and return")

    dry = commands.add_parser("dry-run", help="make the composer post nothing, to try it out")
    dry.add_argument("state", nargs="?", choices=["on", "off"], help="omit to show the setting")

    check = commands.add_parser("check-script", help="compare the posting script with the live one")
    check.add_argument("--reset", action="store_true", help="drop an accepted update, use the vendored copy")
    return root


def _post(args):
    text = compose.message(args.text)
    error = compose.problem(text)
    if error:
        print(error, file=sys.stderr)
        return 1
    return compose.post(text, args.reply, args.image or [], args.dry_run or paths.config()["dry_run"])


def _open(args):
    options = ["--direction", "right"] if args.entrypoint == "feed" else []
    ok, error = herdr.open_pane(args.entrypoint, *options)
    if not ok:
        print(error, file=sys.stderr)
        herdr.notify("/dev/null", error)
    return 0 if ok else 1


def _dry_run(args):
    if args.state:
        paths.save_config({"dry_run": args.state == "on"})
    print(f"Dry run is {'on: nothing is posted' if paths.config()['dry_run'] else 'off'}.")
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "compose":
        return compose.main(args)
    if args.command == "post":
        return _post(args)
    if args.command == "open":
        return _open(args)
    if args.command == "feed":
        return feed.main(args)
    if args.command == "login":
        return login.main(args)
    if args.command == "handle-url":
        return login.handle_url(args.url)
    if args.command == "watch":
        return watch.detach() if args.detach else watch.loop()
    if args.command == "dry-run":
        return _dry_run(args)
    if args.command == "check-script":
        if args.reset:
            script.reset()
        script.check_for_update()
        print(f"Posting with {script.current()}")
        return 0
    return 2
