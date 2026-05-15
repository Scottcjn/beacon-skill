"""Tests for beacon_skill/clawnews_enhanced.py validation functions."""
import pytest
from beacon_skill.clawnews_enhanced import (
    _validate_feed_type,
    _validate_item_type,
    _validate_limit,
    _validate_item_id,
    _validate_text_content,
)


class TestValidateFeedType:
    def test_valid_feeds(self):
        for f in ("ask", "best", "jobs", "new", "show", "skills", "top"):
            assert _validate_feed_type(f) == f

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _validate_feed_type("hackernews")


class TestValidateItemType:
    def test_valid_types(self):
        for t in ("ask", "comment", "job", "show", "skill", "story"):
            assert _validate_item_type(t) == t

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _validate_item_type("poll")


class TestValidateLimit:
    def test_valid(self):
        assert _validate_limit(50) == 50

    def test_positive_boundary(self):
        assert _validate_limit(1) == 1

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            _validate_limit(0)

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            _validate_limit(-1)


class TestValidateItemId:
    def test_valid_int(self):
        assert _validate_item_id(42) == 42

    def test_valid_str(self):
        assert _validate_item_id("42") == 42

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _validate_item_id("not_a_number")


class TestValidateTextContent:
    def test_valid_text(self):
        assert _validate_text_content("hello") == "hello"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _validate_text_content("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            _validate_text_content("   ")

    def test_too_long_raises(self):
        with pytest.raises(ValueError):
            _validate_text_content("x" * 20000)
