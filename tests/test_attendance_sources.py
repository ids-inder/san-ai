"""Source adapters: the tolerant normalizer and the API response classifier."""
import datetime as dt

import pytest

from attendance.sources.base import (STATUS_ACCOUNT_BLOCKED, STATUS_AUTH_REJECTED,
                                     STATUS_ENDPOINT_MISSING, STATUS_OK)
from attendance.sources.csvfile import CsvSource
from attendance.sources.etimeoffice import EtimeOfficeSource
from attendance.sources.normalize import normalize_rows, parse_moment


def codes(payload, **kw):
    return [tuple(r) for r in normalize_rows(payload, **kw)]


def test_in_out_per_day_becomes_two_punches():
    rows = codes({"InOutPunchData": [
        {"Empcode": "E1", "Name": "Asha", "DateString": "03/11/2026",
         "INTime": "09:05", "OUTTime": "18:10"}]})
    assert rows == [("E1", "Asha", "2026-11-03 09:05:00", "in", ""),
                    ("E1", "Asha", "2026-11-03 18:10:00", "out", "")]


def test_overnight_out_time_rolls_to_next_day():
    rows = codes([{"Empcode": "E4", "Date": "03/11/2026",
                   "InTime": "22:00", "OutTime": "06:10"}])
    assert rows[1][2] == "2026-11-04 06:10:00"


def test_separate_date_and_time_columns():
    rows = codes([{"EmpCode": "E2", "EmployeeName": "Ravi", "AttDate": "03-11-2026",
                   "PunchTime": "09:30", "InOut": "IN", "MCID": "DEV7"}])
    assert rows == [("E2", "Ravi", "2026-11-03 09:30:00", "in", "DEV7")]


def test_combined_datetime_and_direction_words():
    assert codes([{"paycode": "E3", "DateTime": "03/11/2026 14:05:00",
                   "Direction": "out"}])[0][3] == "out"


def test_unparseable_rows_are_skipped_not_fatal():
    rows = codes([{"nothing": "useful"},
                  {"Empcode": "E5", "Date": "03/11/2026", "INTime": "10:00"}])
    assert len(rows) == 1 and rows[0][0] == "E5"


def test_bookkeeping_columns_do_not_hijack_the_date():
    rows = codes([{"Empcode": "E8", "UpdatedDate": "01/01/2020",
                   "AttDate": "03/11/2026", "INTime": "08:00"}])
    assert rows[0][2].startswith("2026-11-03")


def test_blank_and_sentinel_times_ignored():
    assert codes([{"Empcode": "E6", "Date": "03/11/2026",
                   "INTime": "--:--", "OUTTime": ""}]) == []


def test_parse_moment_handles_underscore_form():
    assert parse_moment("03/11/2026_09:05") == dt.datetime(2026, 11, 3, 9, 5)


# -- the diagnostic that cost a day of investigation ------------------------

def test_blank_500_means_auth_rejected():
    """An empty, type-less 500 is their auth layer refusing the credential."""
    assert EtimeOfficeSource.classify(500, "", b"")[0] == STATUS_AUTH_REJECTED


def test_json_500_means_credentials_accepted_but_api_broken():
    body = (b'{"Message":"An error has occurred.",'
            b'"ExceptionMessage":"Index was outside the bounds of the array.",'
            b'"ExceptionType":"System.IndexOutOfRangeException"}')
    status, detail = EtimeOfficeSource.classify(
        500, "application/json; charset=utf-8", body)
    assert status == STATUS_ACCOUNT_BLOCKED
    assert "Index was outside" in detail      # their words are surfaced


def test_404_and_200_classified():
    assert EtimeOfficeSource.classify(404, "", b"")[0] == STATUS_ENDPOINT_MISSING
    assert EtimeOfficeSource.classify(200, "application/json", b"[]")[0] == STATUS_OK


def test_composite_basic_username_is_colon_joined():
    src = EtimeOfficeSource("CORP", "user", "pass", admin_flag="true")
    import base64
    raw = base64.b64decode(src._auth().split()[1]).decode()
    assert raw == "CORP:user:pass:true"


def test_dates_keep_their_slashes_in_the_query():
    src = EtimeOfficeSource("C", "u", "p")
    url = src._url("DownloadInOutPunchData",
                   {"Empcode": "ALL", "FromDate": "01/11/2026"})
    assert "FromDate=01/11/2026" in url


# -- CSV ---------------------------------------------------------------------

def test_csv_skips_report_banner_above_the_header(tmp_path):
    f = tmp_path / "export.csv"
    f.write_text(
        "Monthly Attendance Register\n"
        "Printed on 05/11/2026\n"
        "Empcode,Name,Date,INTime,OUTTime\n"
        "E1,Asha,03/11/2026,09:05,18:10\n"
        "E2,Ravi,03/11/2026,09:40,18:00\n")
    got = CsvSource(f).fetch(dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert {r.empcode for r in got} == {"E1", "E2"}
    assert len(got) == 4          # two punches each


def test_csv_semicolon_delimiter(tmp_path):
    f = tmp_path / "semi.csv"
    f.write_text("Empcode;Name;Date;INTime;OUTTime\nE1;Asha;03/11/2026;09:00;18:00\n")
    assert len(CsvSource(f).fetch(dt.date(2026, 11, 1), dt.date(2026, 11, 30))) == 2


def test_csv_date_range_filter(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("Empcode,Date,INTime,OUTTime\n"
                 "E1,03/11/2026,09:00,18:00\n"
                 "E1,20/12/2026,09:00,18:00\n")
    got = CsvSource(f).fetch(dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert all(r.punch_at.startswith("2026-11") for r in got)


def test_csv_missing_file_is_a_clean_error(tmp_path):
    from attendance.sources.base import SourceError
    with pytest.raises(SourceError):
        CsvSource(tmp_path / "nope.csv").fetch(dt.date(2026, 1, 1), dt.date(2026, 1, 2))
