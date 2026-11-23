# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""fmt.one_line: words that came from somewhere else, made safe for one line of a terminal."""
from __future__ import annotations

import pytest

from fmt import one_line


def test_an_ordinary_name_is_left_as_it_is():
    for name in ("proj-86-pr-98 fix tests", "修正テスト", "🐓  two spaces", "", " edges kept "):
        assert one_line(name) == name


@pytest.mark.parametrize("given, made", [
    ("line one\nline two", "line one line two"),
    ("one\r\ntwo\u2028three\u2029four\x85five", "one two three four five"),
    ("fix\ttests", "fix tests"),
    ("red\x1b[31m name", "red[31m name"),               # the escape is gone: what is left is only letters
    ("title\x1b]0;owned\x07", "title]0;owned"),
    ("nul\x00 del\x7f c1\x9b0m", "nul del c10m"),
    ("\x1b\x07\x00", ""),
])
def test_line_breaks_become_spaces_and_control_characters_are_left_out(given, made):
    assert one_line(given) == made
    assert not any(ch < " " or "\x7f" <= ch <= "\x9f" for ch in one_line(given))


def test_whatever_it_is_given_comes_out_as_words():
    assert one_line(7) == "7" and one_line(None) == "None"
