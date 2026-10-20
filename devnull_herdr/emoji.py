"""Emoji by their GitHub shortcode: `:star:` is ⭐.

The shortcodes are GitHub's, from gemoji, kept in vendor/emoji.json.
"""

import functools
import json
import re

from . import paths

DATA = paths.ROOT / "vendor" / "emoji.json"
LIMIT = 5
# A shortcode being typed: a colon at the start of a word, then what there is of the name.
# Not after a letter or digit, so that a link (https://) or a time (10:30) is left alone.
TYPING = re.compile(r"(?:^|(?<=[\s(\[{\"']))(:[A-Za-z0-9_+-]+)$")


@functools.lru_cache(maxsize=1)
def shortcodes():
    """{"star": "⭐", ...}, in gemoji's order. Empty if the file is missing or broken."""
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: char for name, char in data.items() if isinstance(name, str) and isinstance(char, str)} \
        if isinstance(data, dict) else {}


def typing(text, cursor):
    """(start, query) of the shortcode being typed just before `cursor`, or None."""
    line = text[:cursor].rsplit("\n", 1)[-1]
    found = TYPING.search(line) if line else None
    if not found:
        return None
    return cursor - len(found.group(1)), found.group(1)[1:].lower()


def search(query, limit=LIMIT):
    """[(name, emoji)] for a query of two characters or more: names that start with it,
    shortest first, then names that only contain it."""
    names = shortcodes()
    if len(query) < 2:
        return []
    starts = sorted((name for name in names if name.startswith(query)), key=lambda name: (len(name), name))
    within = sorted((name for name in names if query in name and not name.startswith(query)), key=lambda name: (len(name), name))
    return [(name, names[name]) for name in (starts + within)[:limit]]
