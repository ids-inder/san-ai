"""Rules engine: the part that decides whether someone was at work."""
import datetime as dt

import pytest

from attendance.rules import (ABSENT, HALF_DAY, HOLIDAY, INCOMPLETE, PRESENT,
                              SHORT, WEEK_OFF, Shift, compute_day, window_for,
                              work_date_of)

GEN = Shift()                                   # 09:00-18:00, 15m grace
NIGHT = Shift(code="NGT", start_min=22 * 60, end_min=30 * 60)


def test_full_day_present():
    rec = compute_day([("2026-11-03 09:05:00", "in"),
                       ("2026-11-03 18:10:00", "out")], GEN)
    assert rec.status == PRESENT
    assert rec.late_minutes == 0          # inside grace
    assert rec.worked_minutes == 545
    assert rec.anomalies == []


def test_lateness_counted_from_shift_start_not_from_grace():
    rec = compute_day([("2026-11-03 10:02:00", "in"),
                       ("2026-11-03 18:00:00", "out")], GEN)
    assert rec.late_minutes == 62


def test_grace_forgives_small_delay():
    rec = compute_day([("2026-11-03 09:14:00", "in"),
                       ("2026-11-03 18:00:00", "out")], GEN)
    assert rec.late_minutes == 0


def test_break_is_not_paid_as_work():
    """Worked time is the sum of paired intervals, not the first-to-last span."""
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 13:00:00", "out"),
                       ("2026-11-03 14:00:00", "in"),
                       ("2026-11-03 18:00:00", "out")], GEN)
    assert rec.worked_minutes == 480       # not 540
    assert rec.status == PRESENT


def test_missing_out_punch_is_incomplete_not_present():
    rec = compute_day([("2026-11-03 09:00:00", "in")], GEN)
    assert rec.status == INCOMPLETE
    assert "missing_out" in rec.anomalies
    assert rec.worked_minutes == 0


def test_half_day_and_short_thresholds():
    half = compute_day([("2026-11-03 09:00:00", "in"),
                        ("2026-11-03 14:00:00", "out")], GEN)
    assert half.status == HALF_DAY
    short = compute_day([("2026-11-03 09:00:00", "in"),
                         ("2026-11-03 11:00:00", "out")], GEN)
    assert short.status == SHORT


def test_no_punches_absent_weekoff_holiday():
    assert compute_day([], GEN).status == ABSENT
    assert compute_day([], GEN, week_off=True).status == WEEK_OFF
    assert compute_day([], GEN, holiday="Diwali").status == HOLIDAY
    # a holiday outranks a weekly off
    assert compute_day([], GEN, week_off=True, holiday="Diwali").status == HOLIDAY


def test_overtime_only_past_full_day():
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 19:30:00", "out")], GEN)
    assert rec.ot_minutes == 150
    assert compute_day([("2026-11-03 09:00:00", "in"),
                        ("2026-11-03 17:00:00", "out")], GEN).ot_minutes == 0


def test_early_out_flagged():
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 16:00:00", "out")], GEN)
    assert rec.early_out_minutes == 120


def test_night_shift_spans_midnight():
    rec = compute_day([("2026-11-03 22:05:00", "in"),
                       ("2026-11-04 06:10:00", "out")], NIGHT)
    assert rec.status == PRESENT
    assert rec.worked_minutes == 485
    assert rec.late_minutes == 0


def test_night_shift_window_starts_before_midnight():
    start, end = window_for(NIGHT, dt.date(2026, 11, 3))
    assert start == "2026-11-03 19:00:00"
    assert end == "2026-11-04 19:00:00"
    # an 06:10 out-punch belongs to the previous work date
    assert work_date_of(NIGHT, dt.datetime(2026, 11, 4, 6, 10)) == dt.date(2026, 11, 3)
    assert work_date_of(GEN, dt.datetime(2026, 11, 4, 6, 10)) == dt.date(2026, 11, 4)


def test_undirected_punches_pair_chronologically():
    """Many devices export bare timestamps with no in/out marker."""
    rec = compute_day([("2026-11-03 09:00:00", ""),
                       ("2026-11-03 18:00:00", "")], GEN)
    assert rec.status == PRESENT
    assert rec.worked_minutes == 540


def test_double_in_is_flagged():
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 10:00:00", "in"),
                       ("2026-11-03 18:00:00", "out")], GEN)
    assert "double_in" in rec.anomalies


def test_shift_from_row_falls_back_to_defaults():
    assert Shift.from_row(None).start_min == 540
    assert Shift.from_row({"code": "X", "start_min": 600}).start_min == 600


def test_present_bar_is_lower_than_the_overtime_baseline():
    """A 7h25 day is present, not a half day - but earns no overtime.

    present_minutes (7h) and full_day_minutes (8h) are deliberately different
    numbers; collapsing them into one would mislabel every slightly-short day.
    """
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 16:25:00", "out")], GEN)
    assert rec.status == PRESENT
    assert rec.worked_minutes == 445
    assert rec.ot_minutes == 0


def test_thresholds_are_configurable_per_shift():
    strict = Shift(present_minutes=480, half_day_minutes=240)
    rec = compute_day([("2026-11-03 09:00:00", "in"),
                       ("2026-11-03 16:25:00", "out")], strict)
    assert rec.status == HALF_DAY        # same day, stricter clinic policy
