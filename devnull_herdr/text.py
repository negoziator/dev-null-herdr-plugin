"""Text that is shown before it is committed.

What the preview shows must be what is committed. A control character, such as the start of a
terminal escape sequence, could hide or rewrite part of the preview while still being sent, so
it never gets that far: commits, drafts and git subjects are cleaned of them first.
"""

import re

# C0 and C1 controls and DEL, except tab and newline; and the invisible characters that reorder
# or hide text (bidi controls, zero-width space, BOM). The zero-width joiners stay: emoji need them.
CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200b\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def clean(text):
    """The text without control characters, ready to show and to commit."""
    return CONTROL.sub("", text.replace("\r\n", "\n"))


def visible(text):
    """Shows control characters as `\\x1b` and the like, for text that is only displayed."""
    return CONTROL.sub(lambda match: f"\\u{ord(match.group()):04x}" if ord(match.group()) > 0xFF
                       else f"\\x{ord(match.group()):02x}", text)
