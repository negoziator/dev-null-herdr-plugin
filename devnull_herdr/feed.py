"""The feed: /dev/null's Following, Latest and Trending logs, and your notifications, in a pane.

Keys: 1 2 3 switch logs, 4 notifications · j/k or arrows move · g goes to the newest ·
M writes a new post · R replies · O opens in the browser · s stars · Q closes.

The feed refreshes by itself: new posts slot in at the top, marked until you've been on them,
and the post you are reading stays where it is on screen. Opening notifications marks them
read on /dev/null, as the site does, so they load only while that tab is open.
"""

import curses
import datetime
import shutil
import subprocess
import time

from . import api, herdr, login, paths
from .text import clean
from .textbox import width

TABS = [("following", "Following"), ("latest", "Latest"), ("trending", "Trending"), ("notifications", "Notifications")]
REFRESH_EVERY = 30
MAX_BODY_ROWS = 8
VERBS = {
    "replied": "replied to you", "mentioned": "mentioned you", "quoted": "quoted you", "starred": "starred",
    "followed": "followed you", "approved": "your post was approved", "rejected": "your post was rejected",
}
ICONS = {"replied": "↩", "mentioned": "@", "quoted": "❝", "starred": "★", "followed": "+", "approved": "✓", "rejected": "✗"}


def age(created_at, now=None):
    """'37m', '2h', '3d' since an ISO 8601 time."""
    try:
        then = datetime.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    seconds = max(int((now or time.time()) - then.timestamp()), 0)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return "now"


def number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def columns(text):
    return sum(width(char) for char in text)


def fit(text, cols):
    """`text` cut to at most `cols` columns, counting wide characters as two."""
    used, end = 0, 0
    for end, char in enumerate(text):
        if used + width(char) > cols:
            return text[:end]
        used += width(char)
    return text


def wrap(text, cols):
    """Rows of at most `cols` columns, broken at spaces where it can, inside a long word if not."""
    rows = []
    for paragraph in text.split("\n"):
        row = ""
        for word in paragraph.split(" "):
            candidate = f"{row} {word}" if row else word
            if columns(candidate) <= cols:
                row = candidate
                continue
            if row:
                rows.append(row)
            while columns(word) > cols:
                piece = fit(word, cols)
                rows.append(piece)
                word = word[len(piece):]
            row = word
        rows.append(row)
    return rows


def _author(commit):
    author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
    return clean(str(author.get("name") or "")), clean(str(author.get("username") or "?"))


def post_lines(commit, cols):
    """A post as (text, style) rows: style is "head", "body", "dim" or "quote"."""
    name, username = _author(commit)
    head = f"{name} @{username} · {age(commit.get('created_at'))}" if name and name != username else \
        f"@{username} · {age(commit.get('created_at'))}"
    short = str(commit.get("short_hash") or "")
    head = fit(head, max(cols - len(short) - 1, 1))
    lines = [(head + " " * max(cols - len(short) - columns(head), 1) + short, "head")]
    if commit.get("parent_id"):
        lines.append(("↳ a reply", "dim"))
    if commit.get("deleted_at"):
        lines.append(("(reverted)", "dim"))
    rows = wrap(clean(str(commit.get("body") or "")).replace("\t", "    "), max(cols, 10))
    for row in rows[:MAX_BODY_ROWS]:
        lines.append((row, "body"))
    if len(rows) > MAX_BODY_ROWS:
        lines.append(("… O opens all of it", "dim"))
    images = commit.get("images") or []
    if images:
        lines.append((f"[{len(images)} image{'s' if len(images) != 1 else ''}]", "dim"))
    quoted = commit.get("quoted_commit")
    if isinstance(quoted, dict):
        _, quoted_user = _author(quoted)
        excerpt = clean(str(quoted.get("body") or "")).replace("\n", " ")
        lines.append((fit(f"❝ @{quoted_user}: {excerpt}", cols), "quote"))
    star = "★" if commit.get("starred") else "☆"
    lines.append((f"{star} {number(commit.get('stars_count'))}   ↩ {number(commit.get('replies_count'))}", "dim"))
    return lines


def notification_lines(item, cols):
    kind = str(item.get("type") or "")
    actor = item.get("actor") if isinstance(item.get("actor"), dict) else {}
    who = "@" + clean(str(actor.get("username") or "someone"))
    others = number(item.get("actors_count") or 1) - 1
    if others > 0:
        who += f" and {others} other{'s' if others != 1 else ''}"
    verb = VERBS.get(kind, kind)
    head = verb[:1].upper() + verb[1:] if kind in ("approved", "rejected") else f"{who} {verb}"
    lines = [(fit(f"{ICONS.get(kind, '·')} {head} · {age(item.get('created_at'))}", cols), "head")]
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else None
    if commit:
        excerpt = clean(str(commit.get("body") or "")).replace("\n", " ")
        lines.append((fit(excerpt, cols), "body"))
    return lines


class Feed:
    """What the pane shows, apart from drawing: tabs, items, selection, paging."""

    def __init__(self, client=api):
        self.client = client
        self.tab = 0
        self.items = {name: [] for name, _ in TABS}
        self.cursors = {name: None for name, _ in TABS}
        self.loaded = set()
        self.selected = {name: 0 for name, _ in TABS}
        self.top = {name: 0 for name, _ in TABS}  # the first post on screen
        self.fresh = set()  # ids of posts that came in by themselves, not seen yet
        self.note = ""
        self.refreshed = time.monotonic()

    @property
    def name(self):
        return TABS[self.tab][0]

    def current(self):
        items = self.items[self.name]
        return items[self.selected[self.name]] if items else None

    def load(self, more=False):
        name = self.name
        if more and not self.cursors[name]:
            return
        answer = self.client.notifications(self.cursors[name] if more else None) if name == "notifications" else \
            self.client.log(name, cursor=self.cursors[name] if more else None)
        data = [item for item in answer.get("data") or [] if isinstance(item, dict)]
        meta = answer.get("meta") if isinstance(answer.get("meta"), dict) else {}
        self.items[name] = self.items[name] + data if more else data
        self.cursors[name] = meta.get("next_cursor")
        if not more:
            self.selected[name] = self.top[name] = 0
        self.loaded.add(name)
        self.refreshed = time.monotonic()

    def switch(self, tab):
        self.tab = tab
        if self.name not in self.loaded:
            self.load()

    def move(self, step):
        items = self.items[self.name]
        if not items:
            return
        index = min(max(self.selected[self.name] + step, 0), len(items) - 1)
        self.selected[self.name] = index
        self.fresh.discard(items[index].get("id"))
        if index >= len(items) - 3 and self.cursors[self.name]:
            self.load(more=True)

    def refresh(self):
        """Brings the tab up to date without moving what you are reading.

        New posts go on top, and the selection and the screen move down with them, so the post
        you are on stays put. Counts of posts already shown are updated. Trending is ranked and
        so reorders: it refreshes only while you are at its top.
        """
        self.refreshed = time.monotonic()
        name = self.name
        if name not in self.loaded:
            return
        if name in ("trending", "notifications"):
            if self.selected[name] == 0:
                self.load()
            return
        answer = self.client.log(name)
        latest = [item for item in answer.get("data") or [] if isinstance(item, dict) and item.get("id")]
        shown = {item.get("id"): item for item in self.items[name]}
        new = [item for item in latest if item["id"] not in shown]
        for item in latest:
            if item["id"] in shown:
                shown[item["id"]].update(item)
        if not new:
            return
        if len(new) == len(latest):
            # More new posts than a page: start from the top again.
            meta = answer.get("meta") if isinstance(answer.get("meta"), dict) else {}
            self.items[name], self.cursors[name] = latest, meta.get("next_cursor")
            self.selected[name] = self.top[name] = 0
        else:
            self.items[name] = new + self.items[name]
            if self.selected[name] or self.top[name]:
                self.selected[name] += len(new)
                self.top[name] += len(new)
        self.fresh.update(item["id"] for item in new)

    def new_above(self):
        """How many posts that came in by themselves are above the screen, so out of sight."""
        return sum(1 for item in self.items[self.name][: self.top[self.name]] if item.get("id") in self.fresh)

    def toggle_star(self):
        commit = self.current()
        if not commit or self.name == "notifications" or not commit.get("id"):
            return
        answer = self.client.star(commit["id"], on=not commit.get("starred"))
        if isinstance(answer.get("data"), dict):
            commit.update({key: answer["data"].get(key, commit.get(key)) for key in ("starred", "stars_count")})

    def target(self):
        """The post a key acts on: the selected post, or the post a notification is about."""
        item = self.current()
        if item and self.name == "notifications":
            item = item.get("commit") if isinstance(item.get("commit"), dict) else None
        return item


def reply_path():
    return paths.state_dir() / "reply-to.json"


def start_reply(commit):
    """Opens the composer as a reply to `commit`: the popup reads what it replies to from here."""
    _, username = _author(commit)
    paths.write_json(reply_path(), {
        "id": commit.get("id"), "username": username,
        "excerpt": clean(str(commit.get("body") or "")).replace("\n", " ")[:80],
    })
    ok, error = herdr.open_pane("reply")
    return None if ok else error


def start_post():
    """Opens the composer for a new post. Posting needs no sign-in, so this works signed out too."""
    ok, error = herdr.open_pane("compose")
    return None if ok else error


def open_in_browser(url):
    """Opens a link on dev-null.sh, and nothing else: not a file, not another app's scheme."""
    if not isinstance(url, str) or not url.startswith(api.BASE + "/"):
        return
    for opener in ("xdg-open", "open"):
        if shutil.which(opener):
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return


def main(args=None):
    try:
        return curses.wrapper(_loop, Feed())
    except KeyboardInterrupt:
        return 0


def _styles():
    styles = {"head": curses.A_BOLD, "body": curses.A_NORMAL, "dim": curses.A_DIM, "quote": curses.A_DIM,
              "accent": curses.A_BOLD | curses.A_REVERSE}
    if curses.has_colors():
        try:
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_MAGENTA, -1)
            curses.init_pair(2, curses.COLOR_CYAN, -1)
            styles["accent"] = curses.color_pair(1) | curses.A_BOLD
            styles["quote"] = curses.color_pair(2)
        except curses.error:
            pass
    return styles


def _loop(screen, feed):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(1000)
    styles = _styles()
    if api.account():
        _safely(feed, feed.load)
    while True:
        _draw(screen, feed, styles)
        try:
            key = screen.get_wch()
        except curses.error:
            key = None  # a second passed: time to count new posts, maybe
        if key is None:
            if api.account() and time.monotonic() - feed.refreshed >= REFRESH_EVERY:
                _safely(feed, feed.refresh)
            continue
        if key == "Q":
            return 0
        if key == "M":
            feed.note = start_post() or ""
            continue
        if not api.account():
            if key == "l":
                curses.endwin()
                login.main(type("Args", (), {"again": False, "logout": False})())
                try:
                    input("\nPress Enter to go back to the feed.")
                except (EOFError, OSError, KeyboardInterrupt):
                    return 0  # the pane was closed during the sign-in
                screen.refresh()
                if api.account():
                    _safely(feed, feed.load)
            continue
        if key in ("1", "2", "3", "4"):
            _safely(feed, lambda: feed.switch(int(key) - 1))
        elif key in ("\t",):
            _safely(feed, lambda: feed.switch((feed.tab + 1) % len(TABS)))
        elif key in ("j", curses.KEY_DOWN):
            _safely(feed, lambda: feed.move(1))
        elif key in ("k", curses.KEY_UP):
            _safely(feed, lambda: feed.move(-1))
        elif key in (curses.KEY_NPAGE, " "):
            _safely(feed, lambda: feed.move(5))
        elif key == curses.KEY_PPAGE:
            _safely(feed, lambda: feed.move(-5))
        elif key == "g":
            feed.selected[feed.name] = feed.top[feed.name] = 0
        elif key == "R":
            target = feed.target()
            if target and target.get("id"):
                feed.note = start_reply(target) or ""
        elif key == "s":
            _safely(feed, feed.toggle_star)
        elif key == "O":
            target = feed.target()
            if target and target.get("html_url"):
                open_in_browser(target["html_url"])


def _safely(feed, action):
    try:
        action()
        feed.note = ""
    except api.ApiError as error:
        wait = f" Try again in {error.retry_after}s." if error.retry_after else ""
        feed.note = f"{error}{wait}"
    except (TypeError, ValueError, AttributeError, KeyError) as error:
        feed.note = f"/dev/null answered something unexpected ({type(error).__name__})."


def _put(screen, row, column, text, attributes=0):
    try:
        screen.addstr(row, column, text, attributes)
    except curses.error:
        pass


def _draw(screen, feed, styles):
    rows, cols = screen.getmaxyx()
    screen.erase()
    column = 1
    for index, (_, title) in enumerate(TABS):
        label = f" {index + 1} {title} "
        _put(screen, 0, column, label, styles["accent"] if index == feed.tab else curses.A_DIM)
        column += len(label) + 1
    account = api.account()
    if account:
        who = f"@{account['username']} "
        _put(screen, 0, max(cols - len(who), column), who, curses.A_DIM)
    _put(screen, 1, 0, "─" * cols, curses.A_DIM)

    top = 2
    if not account:
        for offset, line in enumerate([
            "Sign in to see your feed.",
            "",
            "Press l to sign in: your browser opens /dev/null, you press Approve.",
            "The plugin uses its own key for this, which you can sign out on",
            "/dev/null at any time (Settings → Devices).",
        ]):
            _put(screen, top + offset, 2, line[: max(cols - 3, 0)])
        _footer(screen, feed, rows, cols, "l sign in · M new post · Q close")
        screen.refresh()
        return
    width = max(cols - 4, 10)
    items = feed.items[feed.name]
    render = notification_lines if feed.name == "notifications" else post_lines
    blocks = [render(item, width) for item in items]
    selected = feed.selected[feed.name]
    above = feed.new_above()
    if above:
        _put(screen, top, 2, f"↑ {above} new above · g to jump", styles["accent"])
        top += 1
    start = scroll(blocks, selected, feed.top[feed.name], rows - top - 2)
    feed.top[feed.name] = start
    row = top
    for index in range(start, len(blocks)):
        if row >= rows - 2:
            break
        for text, style in blocks[index]:
            if row >= rows - 2:
                break
            marker = "▌" if index == selected else ("●" if items[index].get("id") in feed.fresh else " ")
            _put(screen, row, 0, marker, styles["accent"])
            _put(screen, row, 2, text, styles[style] | (curses.A_BOLD if index == selected and style == "body" else 0))
            row += 1
        row += 1
    if not items:
        empty = "Nothing here yet." if feed.name in feed.loaded else "Loading…"
        _put(screen, top, 2, empty, curses.A_DIM)
    keys = "1-4 tabs · j/k move · M new post · R reply · O open · s star · Q close"
    if feed.name == "notifications":
        keys = "1-4 tabs · j/k move · M new post · R reply · O open · Q close"
    _footer(screen, feed, rows, cols, keys)
    screen.refresh()


def scroll(blocks, selected, top, room):
    """The first post on screen: the same as before when the selected post still fits below
    it, else just enough further that the whole selected post shows."""
    top = min(max(top, 0), selected)
    while top < selected and sum(len(block) + 1 for block in blocks[top : selected + 1]) > room:
        top += 1
    return top


def _footer(screen, feed, rows, cols, keys):
    _put(screen, rows - 2, 0, "─" * cols, curses.A_DIM)
    _put(screen, rows - 1, 1, (feed.note or keys)[: max(cols - 2, 0)], curses.A_BOLD if feed.note else curses.A_DIM)
