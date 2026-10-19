"""The composer: a text box for a /dev/null post, right in the popup.

Type the post, see its length as you go, and post it with Ctrl-S. What the box shows is what is
posted: control characters can't be typed or pasted into it.
"""

import curses
import os
import shlex
import sys
import time

from . import drafts, herdr, paths, script
from .text import clean
from .textbox import Box

MIN, MAX = 3, 4000
POST, CANCEL, DISCARD = "post", "cancel", "discard"


def message(text):
    """The post as it will be sent: cleaned and trimmed."""
    return "\n".join(line.rstrip() for line in clean(text).splitlines()).strip()


def problem(text):
    if not text:
        return "Write something first."
    if len(text) < MIN:
        return f"A post needs at least {MIN} characters."
    if len(text) > MAX:
        return f"{len(text) - MAX} characters too long: a post can have up to {MAX}."
    return None


def header(reply, images, draft, dry_run):
    parts = ["/dev/null", "draft post" if draft else "new post"]
    if reply:
        parts.append(f"reply to {reply}")
    if images:
        parts.append(f"{len(images)} image{'s' if len(images) != 1 else ''}")
    if dry_run:
        parts.append("DRY RUN, nothing is sent")
    return " · ".join(parts)


def recovery_path():
    return paths.state_dir() / "unsent-post.json"


def save_unsent(text, reply, images):
    paths.write_json(recovery_path(), {"text": text, "reply": reply, "images": list(images)})


def edit(text, title, draft, rescue=None):
    """Shows the text box until it is posted or closed. Returns (action, text).

    Should the box ever crash, `rescue` is given what was typed, so that it can come back.
    """
    os.environ.setdefault("ESCDELAY", "25")  # Esc closes at once instead of after a second
    if not sys.stdin.isatty():
        raise RuntimeError("The composer needs a terminal: use `bin/devnull post` from a script.")
    box = Box(text)
    try:
        return curses.wrapper(_loop, box, title, draft)
    except Exception as error:
        where = rescue(box.text) if rescue else "nowhere: it is lost"
        raise RuntimeError(f"The composer stopped ({type(error).__name__}: {error}). Your text is saved: {where}.")
    finally:
        # Bracketed paste off again, whatever happened.
        sys.stdout.write(PASTE_OFF)
        sys.stdout.flush()


def _colors():
    try:
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_RED, -1)
        return curses.color_pair(1) | curses.A_BOLD
    except curses.error:  # colors, but no default background: bold has to do
        return curses.A_BOLD


def _loop(screen, box, title, draft):
    curses.raw()  # Ctrl-S and Ctrl-C reach the composer instead of the terminal
    screen.keypad(True)
    # A paste then arrives wrapped in markers, and goes in as text: a Ctrl-S inside it never posts.
    sys.stdout.write(PASTE_ON)
    sys.stdout.flush()
    red = _colors() if curses.has_colors() else curses.A_BOLD
    keys = "Ctrl-S post · Esc close" + (" · Ctrl-D discard draft" if draft else "")
    state = {"note": "", "top": 0}
    while True:
        _draw(screen, box, title, keys, state, red)
        key = _key(screen)
        # Draw once for everything already typed or pasted, not once per character.
        while True:
            result = _handle(screen, box, key, draft, state)
            if result:
                return result
            if not _queued(screen):
                break
            key = _key(screen)


def _draw(screen, box, title, keys, state, red):
    rows, cols = screen.getmaxyx()
    area, text_cols = max(rows - 5, 1), max(cols - 2, 10)
    lines = box.rows(text_cols)
    row, column = box.position(text_cols)
    state["top"] = top = min(max(state["top"], row - area + 1), row)

    screen.erase()
    _put(screen, 0, 1, title[: cols - 2], curses.A_BOLD)
    _put(screen, 1, 0, "─" * cols)
    for offset, (start, end) in enumerate(lines[top : top + area]):
        _put(screen, 2 + offset, 1, box.text[start:end])
    _put(screen, rows - 3, 0, "─" * cols)
    count = len(message(box.text))
    _put(screen, rows - 2, 1, f"{count}/{MAX}", red if count > MAX else curses.A_DIM)
    if state["note"]:
        _put(screen, rows - 2, len(f"{count}/{MAX}") + 3, state["note"][: max(cols - 12, 0)], red)
    _put(screen, rows - 1, 1, keys[: max(cols - 2, 0)], curses.A_DIM)
    try:
        screen.move(max(min(2 + row - top, rows - 4), 0), max(min(1 + column, cols - 1), 0))
    except curses.error:  # a popup too small to hold the box
        pass
    screen.refresh()


def _handle(screen, box, key, draft, state):
    """Applies one key. Returns (action, text) when the box is done, else None."""
    text_cols = max(screen.getmaxyx()[1] - 2, 10)
    confirming, state["note"] = state["note"], ""
    if isinstance(key, tuple):  # a paste: text, whatever it contains
        box.insert(key[1])
    elif key in ("\x13", "\x04") and _queued(screen):
        # More input right behind it: a paste without markers, not a key press. Never act on it.
        state["note"] = "Ignored a Ctrl key inside pasted text."
    elif key == "\x13":  # Ctrl-S
        state["note"] = problem(message(box.text)) or ""
        if not state["note"]:
            return POST, message(box.text)
    elif key in ("\x1b", "\x03"):  # Esc, Ctrl-C: twice, when it would throw text away
        if confirming == CLOSING or not box.text.strip():
            return CANCEL, box.text
        state["note"] = CLOSING
    elif key == "\x04" and draft:  # Ctrl-D: twice, it deletes the draft
        if confirming == DISCARDING:
            return DISCARD, box.text
        state["note"] = DISCARDING
    elif key is None:
        pass  # a key sequence the composer doesn't use
    elif key in ("\x7f", "\x08", curses.KEY_BACKSPACE):
        box.backspace()
    elif key == curses.KEY_DC:
        box.delete()
    elif key in ("\n", "\r", curses.KEY_ENTER):
        box.insert("\n")
    elif key == curses.KEY_LEFT:
        box.left()
    elif key == curses.KEY_RIGHT:
        box.right()
    elif key == curses.KEY_UP:
        box.up(text_cols)
    elif key == curses.KEY_DOWN:
        box.down(text_cols)
    elif key in (curses.KEY_HOME, "\x01"):  # Home, Ctrl-A
        box.home(text_cols)
    elif key in (curses.KEY_END, "\x05"):  # End, Ctrl-E
        box.end(text_cols)
    elif isinstance(key, str):
        box.insert(key)
    return None


CLOSING = "Press Esc again to close without posting."
DISCARDING = "Press Ctrl-D again to throw this draft away."
PASTE_ON, PASTE_OFF = "\x1b[?2004h", "\x1b[?2004l"
PASTE_START, PASTE_END = "[200~", "\x1b[201~"
PASTE_LIMIT = 5
# What terminals send for arrows, Home and End, in normal and in application mode.
SEQUENCES = {
    "[A": curses.KEY_UP, "OA": curses.KEY_UP, "[B": curses.KEY_DOWN, "OB": curses.KEY_DOWN,
    "[C": curses.KEY_RIGHT, "OC": curses.KEY_RIGHT, "[D": curses.KEY_LEFT, "OD": curses.KEY_LEFT,
    "[H": curses.KEY_HOME, "OH": curses.KEY_HOME, "[1~": curses.KEY_HOME, "[F": curses.KEY_END,
    "OF": curses.KEY_END, "[4~": curses.KEY_END, "[3~": curses.KEY_DC,
}


def _key(screen):
    """The next key. A lone Esc is Esc; an Esc followed at once by more is a key sequence curses
    didn't decode: it becomes that key, or None, and never closes the composer by mistake."""
    key = screen.get_wch()
    if key != "\x1b":
        return key
    screen.nodelay(True)
    try:
        rest = _sequence(screen)
    finally:
        screen.nodelay(False)
    if not rest:
        return key
    if rest == PASTE_START:
        return ("paste", _paste(screen))
    return SEQUENCES.get(rest)


def _paste(screen):
    """The text of a bracketed paste, up to its end marker. A paste can arrive in pieces, so
    this waits up to half a second for each; keys never act inside it."""
    text, deadline = "", time.monotonic() + PASTE_LIMIT
    screen.timeout(500)
    try:
        # A paste whose end marker never comes stops swallowing keys after PASTE_LIMIT seconds.
        while not text.endswith(PASTE_END) and time.monotonic() < deadline:
            char = screen.get_wch()
            if isinstance(char, str):
                text += char
    except curses.error:  # no end marker came: what arrived is the paste
        pass
    finally:
        screen.timeout(-1)
    return text[: -len(PASTE_END)] if text.endswith(PASTE_END) else text


def _queued(screen):
    """Whether more input is already waiting, without taking it."""
    screen.nodelay(True)
    try:
        key = screen.get_wch()
    except curses.error:
        return False
    finally:
        screen.nodelay(False)
    if isinstance(key, str):
        curses.unget_wch(key)
    else:
        curses.ungetch(key)
    return True


def _sequence(screen):
    """Reads one escape sequence after its Esc, and no more: keys typed after it stay queued.

    `[` starts a CSI sequence, ended by a byte from @ to ~; `O` is followed by one character.
    """
    rest = ""
    try:
        while len(rest) < 16:
            char = screen.get_wch()
            if not isinstance(char, str):
                break
            rest += char
            if rest[0] == "O" and len(rest) == 2:
                break
            if rest[0] == "[" and len(rest) > 1 and "@" <= char <= "~":
                break
            if rest[0] not in "[O":
                break
    except curses.error:
        pass
    return rest


def _put(screen, row, column, text, attributes=0):
    """Draws text, ignoring the error curses raises for the bottom-right corner."""
    try:
        screen.addstr(row, column, text, attributes)
    except curses.error:
        pass


def run(text="", reply=None, images=(), draft=None, dry_run=False, ask=None, ui=None):
    """Shows the box until the post is sent or given up. `draft` is (path, draft). Returns an exit code."""
    ask, ui = ask or input, ui or edit

    def rescue(typed):
        if draft:
            drafts.save_edit(draft[0], typed)
            return "it comes back when you review the draft again"
        save_unsent(typed, reply, images)
        return "it comes back the next time you open the composer"

    kept = " The draft is kept." if draft else ""
    while True:
        action, text = ui(text, header(reply, images, draft, dry_run), draft is not None, rescue=rescue)
        if action == CANCEL:
            print("Nothing posted." + kept)
            return 1
        if action == DISCARD:
            if drafts.discard(*draft):
                print("Draft discarded.")
            else:
                print("The draft got new commits while you were reviewing it, so it is kept.")
            return 1
        text = message(text)
        if problem(text):
            continue
        code = post(text, reply, images, draft, dry_run, ask)
        if code != 1 or ask("\nPress e to edit the post again, Enter to close: ").strip().lower() != "e":
            return code


def post(text, reply, images, draft, dry_run, ask=None):
    kept = " The draft is kept." if draft else ""
    if dry_run:
        print("Dry run, nothing sent. This post:\n")
        print(text)
        print("\nwould be sent with:\n  " + shlex.join(script.command(text, reply, images)))
        return 0
    script.check_for_update(ask=ask)
    print("Posting…")
    try:
        ok, url, output = script.commit(text, reply, images)
    except KeyboardInterrupt:
        print("\nInterrupted: the post may have been sent. Check your profile on dev-null.sh." + kept)
        return 130
    if not ok:
        if output:
            print(output)
        print("\nNot posted." + kept)
        return 1
    print(f"Posted: {url}" if url else output)
    if url and "pending review" in output:
        print("It is pending review: only you can see it until it is approved.")
    if draft and not drafts.discard(*draft):
        print("The draft got new commits while you were reviewing it: they are still in it.")
    herdr.notify("Posted to /dev/null", url or output, sound="done")
    return 0


def main(args):
    reply, images, draft, text = args.reply, args.image or [], None, args.text or ""
    dry_run = args.dry_run or paths.config()["dry_run"]
    try:
        if args.draft:
            path, data = drafts.newest()
            if data is None:
                print("No drafts yet: one is made when an agent you run in herdr ships a commit.")
                _wait()
                return 1
            draft, text = (path, data), drafts.render(data)
        unsent = None if (draft or text) else paths.read_json(recovery_path(), {}).get("text")
        if unsent:
            # The post a crash saved, with what it was replying to, unless told otherwise.
            saved = paths.read_json(recovery_path(), {})
            text, reply = unsent, reply or saved.get("reply")
            images = images or [image for image in saved.get("images", []) if isinstance(image, str)]
        code = run(text, reply, images, draft, dry_run)
        if unsent:
            # Shown again: posted, or closed on purpose. Either way it has served its turn.
            recovery_path().unlink(missing_ok=True)
        if draft and code == 0 and drafts.count():
            others = drafts.count()
            print(f"{others} more draft{'s' if others != 1 else ''} waiting.")
    except (KeyboardInterrupt, EOFError):
        print("\nNothing posted.")
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
