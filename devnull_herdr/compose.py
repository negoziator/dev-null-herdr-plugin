"""The composer: write a commit in your editor, review it, commit it.

Like `git commit`, the message is edited in $VISUAL or $EDITOR, with help text under a
scissors line. Everything below the scissors is ignored, so a line starting with `#` (a
hashtag, a heading) is part of the commit, not a comment.
"""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from . import drafts, herdr, paths, script
from .text import clean

SCISSORS = "# ------------------------ >8 ------------------------"
HELP = """\
# Write your /dev/null commit above the scissors line, then save and quit.
# Everything from the scissors line down is ignored. An empty commit aborts.
# Between 3 and 4000 characters. You review it before anything is committed."""
MIN, MAX = 3, 4000


def template(text=""):
    return f"{text}\n\n{SCISSORS}\n{HELP}\n"


def message(text):
    """The commit in an edited file: everything above the scissors, cleaned and trimmed."""
    lines = clean(text).splitlines()
    if SCISSORS in lines:
        lines = lines[: lines.index(SCISSORS)]
    return "\n".join(line.rstrip() for line in lines).strip()


def problem(text):
    if not text:
        return "Empty commit."
    if len(text) < MIN:
        return f"A commit needs at least {MIN} characters."
    if len(text) > MAX:
        return f"{len(text)} characters: a commit can have up to {MAX}."
    return None


def editor():
    for candidate in (os.environ.get("VISUAL"), os.environ.get("EDITOR")):
        if candidate:
            try:
                return shlex.split(candidate)
            except ValueError as error:
                raise RuntimeError(f"Cannot read the editor command {candidate!r}: {error}")
    for fallback in ("nano", "vim", "vi"):
        if shutil.which(fallback):
            return [fallback]
    raise RuntimeError("No editor found: set $EDITOR.")


def edit(text):
    """The commit after editing `text`. An editor that fails (vim's :cq) aborts the commit.

    The editor must wait until you are done: `code --wait`, not `code`.
    """
    command = editor()
    fd, path = tempfile.mkstemp(prefix="COMMIT_EDITMSG.", dir=paths.state_dir())
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(template(text))
        try:
            status = subprocess.run([*command, path]).returncode
        except OSError as error:
            raise RuntimeError(f"Could not start the editor {command[0]}: {error}")
        if status != 0:
            raise RuntimeError(f"The editor exited with status {status}: nothing committed.")
        with open(path) as handle:
            return message(handle.read())
    finally:
        os.remove(path)


def preview(text, reply, images, extra=""):
    """Prints the commit. One taller than the terminal is cut short: [v] shows all of it."""
    size = shutil.get_terminal_size((84, 26))
    cols, room = max(size.columns, 20), max(size.lines - 9, 3)
    shown, rows = [], 0
    for line in text.splitlines():
        # A long line wraps over several rows: count those, not lines.
        needed = max(1, -(-len(line) // cols))
        if rows + needed > room:
            shown.append(line[: (room - rows) * cols])
            break
        shown.append(line)
        rows += needed
    lines = text.splitlines()
    print("\033[2J\033[H", end="")
    print("─" * 60)
    print("\n".join(shown))
    if shown != lines:
        print("… cut short to fit: press v to see all of it")
    print("─" * 60)
    details = [f"{len(text)}/{MAX} characters", f"{len(lines)} lines"]
    if reply:
        details.append(f"reply to {reply}")
    if images:
        details.append(f"{len(images)} image{'s' if len(images) != 1 else ''}")
    print(" · ".join(details) + extra)


def _view(text):
    pager = shutil.which("less")
    if pager:
        subprocess.run([pager], input=text, text=True)
    else:
        print(text)


def run(text="", reply=None, images=(), draft=None, dry_run=False, ask=None):
    """The interactive loop. `draft` is (path, draft) when starting from one. Returns an exit code."""
    ask = ask or input
    kept = " The draft is kept." if draft else ""
    text = edit(text)
    while True:
        error = problem(text)
        if error and not text:
            print("Aborted: empty commit." + kept)
            return 1
        preview(text, reply, images, (" · drafted from git" if draft else "") + (" · DRY RUN" if dry_run else ""))
        if error:
            print(f"\n{error}")
        keys = "[e]dit  [v]iew  [q]uit" if error else "[c]ommit  [e]dit  [v]iew  [q]uit"
        if draft:
            keys += "  [d]iscard draft"
        choice = ask(f"\n{keys} > ").strip().lower()
        if choice == "e":
            text = edit(text)
        elif choice == "v":
            _view(text)
        elif choice == "q":
            print("Nothing committed." + kept)
            return 1
        elif choice == "d" and draft:
            if drafts.discard(*draft):
                print("Draft discarded.")
            else:
                print("The draft got new commits while you were reviewing it, so it is kept.")
            return 1
        elif choice == "c" and not error:
            return commit(text, reply, images, draft, dry_run, ask)


def commit(text, reply, images, draft, dry_run, ask=None):
    kept = " The draft is kept." if draft else ""
    if dry_run:
        print("\nDry run, nothing sent. Would run:\n  " + shlex.join(script.command(text, reply, images)))
        return 0
    print()
    script.check_for_update(ask=ask)
    try:
        ok, url, output = script.commit(text, reply, images)
    except KeyboardInterrupt:
        print("\nInterrupted: the commit may have been sent. Check your profile on dev-null.sh." + kept)
        return 130
    if output:
        print(output)
    if not ok:
        print("\nNot committed." + kept)
        return 1
    if draft and not drafts.discard(*draft):
        print("The draft got new commits while you were reviewing it: they are still in it.")
    herdr.notify("Committed to /dev/null", url or output, sound="done")
    return 0


def main(args):
    reply, images, draft, text = args.reply, args.image or [], None, args.text or ""
    dry_run = args.dry_run or paths.config()["dry_run"]
    if dry_run:
        print("Dry run: nothing will be sent. `bin/devnull dry-run off` to commit for real.")
    try:
        if args.draft:
            path, data = drafts.newest()
            if data is None:
                print("No drafts yet: one is made when an agent you run in herdr commits something.")
                _wait()
                return 1
            draft, text = (path, data), drafts.render(data)
            others = drafts.count() - 1
            if others:
                print(f"{others} more draft{'s' if others != 1 else ''} after this one.")
        code = run(text, reply, images, draft, dry_run)
    except (KeyboardInterrupt, EOFError):
        print("\nNothing committed.")
        code = 130
    except (RuntimeError, OSError) as error:
        print(error)
        code = 1
    _wait()
    return code


def _wait():
    """Keeps a popup open until the result has been read."""
    if os.environ.get("HERDR_PLUGIN_ENTRYPOINT_ID") and sys.stdin.isatty():
        try:
            input("\nPress Enter to close.")
        except (KeyboardInterrupt, EOFError):
            pass
