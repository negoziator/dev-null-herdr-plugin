"""The text box the composer draws: text, a cursor, and how it wraps on screen.

Kept apart from curses so that editing and wrapping can be tested without a terminal.
"""

import unicodedata

from .text import CONTROL


# Joiners and the emoji variation selector take no room; nor do the emoji tag characters,
# which spell out subdivision flags such as the one for Scotland.
ZERO_WIDTH = "\u200c\u200d\ufe0f"
TAGS = range(0xE0020, 0xE0080)


def width(char):
    """Columns a character takes: emoji and CJK take two, combining marks none."""
    if unicodedata.combining(char) or char in ZERO_WIDTH or ord(char) in TAGS:
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def typable(char):
    """Whether a typed or pasted character goes into the post. Control characters never do;
    the joiners and tags emoji are built with do."""
    return char == "\n" or char in ZERO_WIDTH or ord(char) in TAGS or (char.isprintable() and not CONTROL.match(char))


class Box:
    def __init__(self, text=""):
        # Through insert, so a draft or a starting text is filtered like anything typed.
        self.text, self.cursor = "", 0
        self.insert(text)

    def insert(self, chars):
        chars = chars.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
        chars = "".join(char for char in chars if typable(char))
        self.text = self.text[: self.cursor] + chars + self.text[self.cursor :]
        self.cursor += len(chars)

    def backspace(self):
        if self.cursor:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1

    def delete(self):
        self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]

    def left(self):
        self.cursor = max(self.cursor - 1, 0)

    def right(self):
        self.cursor = min(self.cursor + 1, len(self.text))

    def rows(self, cols):
        """Where each screen row starts and ends in the text, wrapping at `cols` columns."""
        rows, start, used = [], 0, 0
        for index, char in enumerate(self.text):
            if char == "\n":
                rows.append((start, index))
                start, used = index + 1, 0
                continue
            if used + width(char) > cols:
                rows.append((start, index))
                start, used = index, 0
            used += width(char)
        rows.append((start, len(self.text)))
        return rows

    def position(self, cols):
        """(row, column) of the cursor on screen."""
        rows = self.rows(cols)
        for number, (start, end) in enumerate(rows):
            last = number == len(rows) - 1
            # The cursor at the end of a wrapped row shows at the start of the next one.
            if start <= self.cursor < end or (self.cursor == end and (last or self.text[end : end + 1] == "\n")):
                return number, sum(width(char) for char in self.text[start : self.cursor])
        return len(rows) - 1, 0

    def _to_row(self, cols, step):
        rows = self.rows(cols)
        row, column = self.position(cols)
        target = row + step
        if not 0 <= target < len(rows):
            self.cursor = 0 if step < 0 else len(self.text)
            return
        start, end = rows[target]
        used, self.cursor = 0, start
        while self.cursor < self._last(rows, target) and used + width(self.text[self.cursor]) <= column:
            used += width(self.text[self.cursor])
            self.cursor += 1

    def up(self, cols):
        self._to_row(cols, -1)

    def down(self, cols):
        self._to_row(cols, 1)

    def home(self, cols):
        self.cursor = self.rows(cols)[self.position(cols)[0]][0]

    def end(self, cols):
        rows = self.rows(cols)
        self.cursor = self._last(rows, self.position(cols)[0])

    def _last(self, rows, number):
        """The furthest the cursor goes on a row. On a row that wraps, the end of the row is
        where the next one starts, so the cursor stops one character before it."""
        start, end = rows[number]
        wraps = number < len(rows) - 1 and self.text[end : end + 1] != "\n"
        if not wraps:
            return end
        # Not in the middle of an emoji or before a combining mark: at the start of that cluster.
        last = max(end - 1, start)
        while last > start and (width(self.text[last]) == 0 or self.text[last - 1] == "\u200d"):
            last -= 1
        return last
