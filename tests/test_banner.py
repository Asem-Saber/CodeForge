import io

import pytest
from rich.console import Console

import src
from src.ui.banner import WORDMARK, WORDMARK_ASCII, render_banner, wordmark_for
from src.ui.console import THEME, ConsoleUI


def console(width=100, legacy=False):
    con = Console(file=io.StringIO(), width=width, force_terminal=False, no_color=True)
    con.legacy_windows = legacy
    con.push_theme(THEME)
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


class FakeSession:
    def __init__(self, session_id="20261007-a4f1", files=("a.py", "b.py", "c.py")):
        self.session_id = session_id
        self._files = list(files)

    def files(self):
        return list(self._files)


def rendered(width=100, resumed=False, session=None, legacy=False):
    con = console(width=width, legacy=legacy)
    con.print(render_banner(session or FakeSession(), resumed, con))
    return con.file.getvalue()


class TestBannerContent:
    def test_shows_the_version_in_the_title(self):
        assert f"CodeForge v{src.__version__}" in rendered()

    def test_shows_the_session_id(self):
        assert "20261007-a4f1" in rendered()

    def test_shows_the_file_count(self):
        assert "3 files" in rendered()

    def test_shows_the_model(self):
        assert "test-model" in rendered()

    def test_shows_the_endpoint_host_only(self):
        output = rendered()
        assert "localhost:1234" in output
        assert "http://" not in output

    def test_never_shows_the_api_key(self):
        assert "test-key" not in rendered()

    def test_never_shows_credentials_embedded_in_the_endpoint(self, monkeypatch):
        import src.ui.banner as banner_module

        monkeypatch.setattr(banner_module, "ENDPOINT", "https://user:s3cret@api.example.com/v1")
        output = rendered()
        assert "api.example.com" in output
        assert "s3cret" not in output and "user" not in output

    def test_new_session_label(self):
        assert "New session" in rendered(resumed=False)

    def test_resumed_label(self):
        assert "Resuming" in rendered(resumed=True)

    def test_singular_file_count(self):
        assert "1 file" in rendered(session=FakeSession(files=("only.py",)))

    def test_empty_workspace(self):
        assert "no files" in rendered(session=FakeSession(files=()))


class TestBannerWidths:
    @pytest.mark.parametrize("width", [120, 100, 96, 95, 80, 72, 71, 60, 50, 40])
    def test_no_line_exceeds_the_console_width(self, width):
        for line in rendered(width=width).splitlines():
            assert len(line) <= width

    @pytest.mark.parametrize("width", [120, 96, 80, 72])
    def test_wide_enough_terminals_keep_the_art(self, width):
        assert "█" in rendered(width=width)

    @pytest.mark.parametrize("width", [71, 60, 50, 40])
    def test_narrow_terminals_drop_the_art(self, width):
        output = rendered(width=width)
        assert "█" not in output
        assert "CodeForge" in output

    def test_two_column_layout_draws_an_inner_divider(self):
        # Three bars on a row means left border, divider, right border.
        assert any(line.count("│") == 3 for line in rendered(width=100).splitlines())

    def test_stacked_layout_has_no_inner_divider(self):
        assert all(line.count("│") <= 2 for line in rendered(width=80).splitlines())

    def test_legacy_windows_uses_ascii_art(self):
        output = rendered(legacy=True)
        assert "█" not in output
        assert "#" in output


class TestBannerWiring:
    def test_console_ui_prints_the_panel(self):
        buffer = io.StringIO()
        con = Console(file=buffer, width=100, force_terminal=False, no_color=True)
        ConsoleUI(console=con).banner(FakeSession(), resumed=False)
        assert "CodeForge" in buffer.getvalue()
        assert "20261007-a4f1" in buffer.getvalue()
