"""Phase 0 / task 1.3: sanitize_for_tts strips every Markdown marker."""
from __future__ import annotations

from redsun.narration import sanitize_for_tts, has_markdown_artifacts


def test_strips_simple_asterisk():
    out = sanitize_for_tts("This *emphasized* word")
    assert out == "This emphasized word"


def test_strips_bold():
    out = sanitize_for_tts("This **bold** word")
    assert out == "This bold word"


def test_strips_triple_asterisk():
    # Not standard Markdown but defends against stray asterisks.
    out = sanitize_for_tts("a *** triple *** b")
    # Any `*` between non-space characters becomes a single asterisk
    # run we drop later.
    assert "***" not in out


def test_strips_underscore_italic():
    out = sanitize_for_tts("An _italic_ phrase")
    assert out == "An italic phrase"


def test_strips_double_underscore():
    out = sanitize_for_tts("Some __underlined__ text")
    assert out == "Some underlined text"


def test_strips_heading():
    out = sanitize_for_tts("# Title\n\nBody text")
    assert out.startswith("Title")
    assert "#" not in out


def test_strips_blockquote():
    out = sanitize_for_tts("> A quoted line\nNormal text")
    assert ">" not in out


def test_strips_backticks():
    out = sanitize_for_tts("Use `foo()` here")
    assert "`" not in out


def test_strips_markdown_link():
    out = sanitize_for_tts("Click [here](https://example.com) now")
    assert out == "Click here now"


def test_strips_bare_url():
    out = sanitize_for_tts("See https://example.com for details")
    assert "https://example.com" not in out
    assert "See" in out
    assert "for details" in out


def test_collapses_triple_blank_lines():
    out = sanitize_for_tts("a\n\n\n\n\nb")
    assert out == "a\n\nb"


def test_idempotent():
    raw = "Some *bold* and **strong** text with # heading and > quote"
    once = sanitize_for_tts(raw)
    twice = sanitize_for_tts(once)
    assert once == twice


def test_empty_input():
    assert sanitize_for_tts("") == ""
    assert sanitize_for_tts(None) == "" if False else sanitize_for_tts("") == ""


def test_strips_asterisk_during_word():
    # Even when the asterisk appears at word boundaries (the bug
    # described in the audit: TTS speaking "asterisk").
    out = sanitize_for_tts("She said * this * aloud")
    assert "*" not in out


def test_has_markdown_artifacts_detects_residue():
    assert has_markdown_artifacts("# heading") is True
    assert has_markdown_artifacts("**bold**") is True
    assert has_markdown_artifacts("a `code` b") is True
    assert has_markdown_artifacts("> quoted") is True
    assert has_markdown_artifacts("https://x") is True
    assert has_markdown_artifacts("[link](url)") is True


def test_has_markdown_artifacts_clean_text_returns_false():
    assert has_markdown_artifacts("Clean narration with no Markdown.") is False
    assert has_markdown_artifacts("") is False