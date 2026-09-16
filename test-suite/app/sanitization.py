# exercises/test-suite/app/sanitization.py
# L10 sanitizer, shared by every schema that accepts user text
#
# In L10 this lived in routers/items.py, the only router there was. L11 adds
# task endpoints that store user text too, so it moved here: one sanitizer,
# imported by schemas/task.py and routers/items.py, rather than two copies
# that could drift apart.
#
# Key concept: input sanitization prevents stored XSS. If user-submitted text
# is later placed into a page without escaping, any markup in it runs as
# markup. Stripping tags before the value is stored means a careless template
# later on cannot turn stored text into a script.
#
# The L10 starter's hint — one re.sub() over <...> — is not enough on its own,
# and verify_security.py section [2] proves it. A tag that is never closed has
# no ">" for the pattern to find, so it survives untouched:
#
#     <img src=x onerror=alert(1)//
#
# Stored like that and rendered inside <div>{name}</div>, the browser uses
# the ">" from the closing </div> to finish the img tag, and onerror fires.
# The payload does not need its own ">" because the page supplies one.
#
# So sanitize_text() makes two passes:
#
#   1. Remove every complete tag: "<", a character that can start a tag, then
#      anything up to ">". Repeated until nothing changes, because removing
#      one tag can join the text on either side of it into a new one:
#
#          <<b>script>   ->   <script>
#
#      Each round makes the string shorter, so the loop always ends.
#
#   2. Remove an unterminated tag: a "<" that starts a tag, through to the end
#      of the string. That is exactly how a browser reads it, as a tag whose
#      attributes run to the end of the text, so it is removed the same way
#      a complete tag is.
#
# "Can start a tag" means an ASCII letter, "/", "!" or "?" — the characters
# after which the HTML parser leaves text mode. That condition is what keeps
# ordinary text intact. Without it, "3 < 5 and 5 > 3" looks like one long tag
# from the first "<" to the first ">" and is reduced to "3  3", which
# verify_security.py section [1] caught in an earlier version.
#
# Two things this deliberately does not do:
#
#   - Decode HTML entities. "&lt;script&gt;" stays as those literal
#     characters. Decoding AFTER stripping would manufacture a real <script>
#     tag out of text that had just passed the check.
#   - Remove the text between tags. "<script>alert(1)</script>" becomes the
#     plain text "alert(1)", which is inert: it is only dangerous as markup.
#
# Sanitizing input is a second line of defence. The first is escaping on
# output (Jinja2 and React both do it by default), because only the output
# side knows the context: HTML body, attribute, URL, or JavaScript.

import re
import unicodedata

_COMPLETE_TAG = re.compile(r"<[A-Za-z/!?][^>]*>")
_UNTERMINATED_TAG = re.compile(r"<[A-Za-z/!?][\s\S]*")


def sanitize_text(value: str) -> str:
    """Removes HTML tags and surrounding whitespace from user-submitted text.

    Whitespace is stripped LAST. Stripping first would leave the space in
    "<b> </b>Widget" behind once the tags were gone, and the stored name
    would be " Widget".
    """
    without_tags = value
    while True:
        stripped = _COMPLETE_TAG.sub("", without_tags)
        if stripped == without_tags:
            break
        without_tags = stripped

    without_unterminated = _UNTERMINATED_TAG.sub("", without_tags)
    return without_unterminated.strip()


# Characters that occupy a position in a string but display as nothing on
# their own. Not whitespace to str.strip(), so without this a name of two
# zero-width spaces would pass the "must contain text" rule and show up as a
# blank row. Counted as invisible:
#
#   Cf  format characters: zero-width space, BOM, soft hyphen
#   Cc  control characters: NUL and friends
#   Mn  nonspacing marks: a combining accent (U+0301), a variation selector
#       (U+FE0F), the combining grapheme joiner (U+034F)
#   Me  enclosing marks: a combining enclosing circle (U+20DD)
#
# plus the few letters and symbols that render blank and are known for being
# used as invisible names. Mn and Me were added after edge-case probing found
# titles made only of them stored as blank-looking tasks. A mark attached to
# a real letter ("e" + U+0301) is fine: the letter is what makes it visible.
_INVISIBLE_CATEGORIES = ("Cf", "Cc", "Mn", "Me")
_BLANK_LOOKING = {"\u115f", "\u1160", "\u2800", "\u3164", "\uffa0"}


def has_visible_text(text: str) -> bool:
    """True if `text` contains at least one character that displays."""
    return any(
        not char.isspace()
        and unicodedata.category(char) not in _INVISIBLE_CATEGORIES
        and char not in _BLANK_LOOKING
        for char in text
    )
