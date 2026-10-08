from datetime import datetime, timedelta, timezone

from src.ui.sessions import format_age

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def ago(**kwargs):
    return NOW - timedelta(**kwargs)


class TestFormatAge:
    def test_none_is_unknown(self):
        assert format_age(None, NOW) == "unknown"

    def test_seconds_is_just_now(self):
        assert format_age(ago(seconds=5), NOW) == "just now"

    def test_just_under_a_minute(self):
        assert format_age(ago(seconds=59), NOW) == "just now"

    def test_one_minute(self):
        assert format_age(ago(minutes=1), NOW) == "1m ago"

    def test_fifty_nine_minutes(self):
        assert format_age(ago(minutes=59), NOW) == "59m ago"

    def test_one_hour(self):
        assert format_age(ago(hours=1), NOW) == "1h ago"

    def test_twenty_three_hours(self):
        assert format_age(ago(hours=23), NOW) == "23h ago"

    def test_one_day_is_yesterday(self):
        assert format_age(ago(hours=25), NOW) == "yesterday"

    def test_two_days(self):
        assert format_age(ago(days=2), NOW) == "2d ago"

    def test_a_week(self):
        assert format_age(ago(days=7), NOW) == "7d ago"

    def test_naive_timestamp_is_treated_as_utc(self):
        naive = ago(hours=2).replace(tzinfo=None)
        assert format_age(naive, NOW) == "2h ago"

    def test_future_timestamp_clamps_to_just_now(self):
        assert format_age(NOW + timedelta(hours=1), NOW) == "just now"

    def test_now_defaults_to_the_clock(self):
        assert format_age(datetime.now(timezone.utc)) == "just now"
