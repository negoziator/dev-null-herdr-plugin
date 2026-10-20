"""The composer: a text box for a /dev/null post, right in the popup.

Type the post, see its length as you go, and post it with Ctrl-S. What the box shows is what is
posted: control characters can't be typed or pasted into it.

Emoji go in by their GitHub shortcode: type `:st` and a list of matches opens under the cursor.
Up and Down choose, Tab or Enter puts the emoji in, Esc hides the list. Typing the closing colon
of a whole shortcode, as in `:star:`, puts its emoji in as well.
"""

import curses
import os
import shlex
import sys
import time

from . import emoji, herdr, paths, script
from .text import clean
from .textbox import Box, width

MIN, MAX = 3, 4000
POST, CANCEL = "post", "cancel"


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


def header(reply, images, dry_run, reply_label=None):
    kind = f"reply to {reply_label or reply}" if reply else "new post"
    parts = ["/dev/null", kind]
    if images:
        parts.append(f"{len(images)} image{'s' if len(images) != 1 else ''}")
    if dry_run:
        parts.append("DRY RUN, nothing is sent")
    return " · ".join(parts)


def recovery_path():
    return paths.state_dir() / "unsent-post.json"


def save_unsent(text, reply, images):
    paths.write_json(recovery_path(), {"text": text, "reply": reply, "images": list(images)})


def edit(text, title, rescue=None):
    """Shows the text box until it is posted or closed. Returns (action, text).

    Should the box ever crash, `rescue` is given what was typed, so that it can come back.
    """
    os.environ.setdefault("ESCDELAY", "25")  # Esc closes at once instead of after a second
    if not sys.stdin.isatty():
        raise RuntimeError("The composer needs a terminal: use `bin/devnull post` from a script.")
    box = Box(text)
    try:
        return curses.wrapper(_loop, box, title)
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


def _loop(screen, box, title):
    curses.raw()  # Ctrl-S and Ctrl-C reach the composer instead of the terminal
    screen.keypad(True)
    # A paste then arrives wrapped in markers, and goes in as text: a Ctrl-S inside it never posts.
    sys.stdout.write(PASTE_ON)
    sys.stdout.flush()
    red = _colors() if curses.has_colors() else curses.A_BOLD
    keys = "Ctrl-S post · Esc close"
    state = {"note": "", "top": 0, "pick": 0, "hidden": None}
    while True:
        _draw(screen, box, title, keys, state, red)
        key = _key(screen)
        # Draw once for everything already typed or pasted, not once per character.
        while True:
            result = _handle(screen, box, key, state)
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
    cursor_row = 2 + row - top
    # The list goes under the cursor's row, or over it: never on it, nor on the footer.
    below, above = rows - 4 - cursor_row, cursor_row - 2
    state["room"] = below if below >= emoji.LIMIT else max(below, above)
    start, matches = suggestions(box, state)
    if matches:
        keys = "Tab/Enter put it in · ↑↓ choose · Esc hide"
        first = cursor_row + 1 if below >= len(matches) else cursor_row - len(matches)
        _list(screen, matches, state["pick"], first, cols)
    _put(screen, rows - 1, 1, keys[: max(cols - 2, 0)], curses.A_DIM)
    try:
        screen.move(max(min(2 + row - top, rows - 4), 0), max(min(1 + column, cols - 1), 0))
    except curses.error:  # a popup too small to hold the box
        pass
    screen.refresh()


def suggestions(box, state):
    """(start, [(name, emoji)]) for the shortcode being typed, unless Esc hid its list."""
    typing = emoji.typing(box.text, box.cursor)
    if not typing:
        state["hidden"] = None  # out of that shortcode: the next one gets its list again
    if not typing or typing[0] == state.get("hidden"):
        return None, []
    matches = emoji.search(typing[1], limit=max(min(state.get("room", emoji.LIMIT), emoji.LIMIT), 0))
    state["pick"] = min(state.get("pick", 0), max(len(matches) - 1, 0))
    return typing[0], matches


def _list(screen, matches, pick, first, cols):
    """The matching emoji, one per row from row `first`, each row as wide as the box."""
    for offset, (name, char) in enumerate(matches):
        line = f" {'❯' if offset == pick else ' '} {char}  :{name}:"
        line += " " * max(cols - 2 - sum(width(c) for c in line), 0)
        _put(screen, first + offset, 1, line, curses.A_BOLD | curses.A_REVERSE if offset == pick else curses.A_NORMAL)


def _handle(screen, box, key, state):
    """Applies one key. Returns (action, text) when the box is done, else None."""
    text_cols = max(screen.getmaxyx()[1] - 2, 10)
    confirming, state["note"] = state["note"], ""
    start, matches = suggestions(box, state)
    if matches and key in (curses.KEY_UP, curses.KEY_DOWN):
        state["pick"] = (state["pick"] + (1 if key == curses.KEY_DOWN else -1)) % len(matches)
        return None
    # Not with more input right behind it: that is a paste without markers, whose Enter and Tab are text.
    if matches and key in ("\t", "\n", "\r", curses.KEY_ENTER) and not _queued(screen):
        box.complete(start, matches[state["pick"]][1])
        state["pick"] = 0
        return None
    if matches and key == "\x1b":  # Esc hides the list, and closes nothing
        state["hidden"] = start
        return None
    typing = emoji.typing(box.text, box.cursor)
    if key == ":" and typing and typing[1] in emoji.shortcodes() and not _queued(screen):
        box.complete(typing[0], emoji.shortcodes()[typing[1]])
        return None
    state["pick"] = 0  # anything else changes the list: the best match first again
    if isinstance(key, tuple):  # a paste: text, whatever it contains
        box.insert(key[1])
    elif key == "\x13" and _queued(screen):
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


def run(text="", reply=None, images=(), dry_run=False, ask=None, ui=None, reply_label=None):
    """Shows the box until the post is sent or given up. Returns an exit code."""
    ask, ui = ask or input, ui or edit

    def rescue(typed):
        save_unsent(typed, reply, images)
        return "it comes back the next time you open the composer"

    while True:
        action, text = ui(text, header(reply, images, dry_run, reply_label), rescue=rescue)
        if action == CANCEL:
            print("Nothing posted.")
            return 1
        text = message(text)
        if problem(text):
            continue
        code = post(text, reply, images, dry_run, ask)
        if code != 1 or ask("\nPress e to edit the post again, Enter to close: ").strip().lower() != "e":
            return code


def post(text, reply, images, dry_run, ask=None):
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
        print("\nInterrupted: the post may have been sent. Check your profile on dev-null.sh.")
        return 130
    if not ok:
        if output:
            print(output)
        print("\nNot posted.")
        return 1
    print(f"Posted: {url}" if url else output)
    if url and "pending review" in output:
        print("It is pending review: only you can see it until it is approved.")
    herdr.notify("Posted to /dev/null", url or output, sound="done")
    return 0


def main(args):
    reply, images, text = args.reply, args.image or [], args.text or ""
    dry_run = args.dry_run or paths.config()["dry_run"]
    reply_label = None
    if getattr(args, "pending_reply", False) is True:
        # Picked in the feed: what it replies to waits in the state directory, read once.
        picked = paths.read_json(paths.state_dir() / "reply-to.json", {})
        (paths.state_dir() / "reply-to.json").unlink(missing_ok=True)
        if not isinstance(picked.get("id"), str):
            print("Nothing to reply to: pick a post in the feed and press r.")
            _wait()
            return 1
        reply = picked["id"]
        reply_label = f"@{picked.get('username')}: {picked.get('excerpt') or ''}"[:60]
    try:
        unsent = None if text else paths.read_json(recovery_path(), {}).get("text")
        if unsent:
            # The post a crash saved, with what it was replying to, unless told otherwise.
            saved = paths.read_json(recovery_path(), {})
            text, reply = unsent, reply or saved.get("reply")
            images = images or [image for image in saved.get("images", []) if isinstance(image, str)]
        code = run(text, reply, images, dry_run, reply_label=reply_label)
        if unsent:
            # Shown again: posted, or closed on purpose. Either way it has served its turn.
            recovery_path().unlink(missing_ok=True)
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
