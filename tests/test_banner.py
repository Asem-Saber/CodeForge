import io

import pytest
from rich.console import Console

import src
from src.ui.banner import WORDMARK, WORDMARK_ASCII, wordmark_for


def console(width=100, legacy=False):
    con = Console(file=io.StringIO(), width=width, force_terminal=False, no_color=True)
    con.legacy_windows = legacy
    return con


class TestWordmark:
    def test_block_rows_are_equal_length(self):
        assert len({len(row) for row in WORDMARK}) == 1

    def test_ascii_rows_are_equal_length(self):
        assert len({len(row) for row in WORDMARK_ASCII}) == 1

    def test_both_variants_are_the_same_shape(self):
        assert [len(row) for row in WORDMARK] == [len(row) for row in WORDMARK_ASCII]

    def test_ascii_variant_has_no_block_glyphs(self):
        assert not any("█" in row for row in WORDMARK_ASCII)

    def test_legacy_windows_gets_the_ascii_variant(self):
        assert wordmark_for(console(legacy=True)) == WORDMARK_ASCII

    def test_modern_terminal_gets_the_block_variant(self):
        assert wordmark_for(console()) == WORDMARK


class TestVersion:
    def test_version_is_a_dotted_string(self):
        assert src.__version__.count(".") >= 1
