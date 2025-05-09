import datetime
from typing import Annotated, override
import typer
from timesheetsync import MutuallyExclusiveOption, parse_date_range
from typer.testing import CliRunner
import pytest

import timesheetsync

runner = CliRunner()


@pytest.mark.parametrize(
    ("value", "expected_output", "expected_code"),
    [
        ("--help", "DAYS", 0),
        ("--help", "< >...", 0),
        ("-d 23", "days-type: <class 'int'>, daterange-type: <class 'NoneType'>", 0),
        ("-d 23", "days: 23, daterange: None", 0),
        (
            "--days 23",
            "days-type: <class 'int'>, daterange-type: <class 'NoneType'>",
            0,
        ),
        ("--days 23", "days: 23, daterange: None", 0),
        ("-r testa testb", "daterange-type: <class 'tuple'>", 0),
        ("-r testa testb", "days: None, daterange: ('testa', 'testb')", 0),
        ("--daterange testa testb", "daterange-type: <class 'tuple'>", 0),
        ("--daterange testa testb", "days: None, daterange: ('testa', 'testb')", 0),
        (
            "-d 23 -r testa testb",
            "Illegal usage: `daterange` is mutually exclusive with arguments",
            2,
        ),
        (
            "--days 23 --daterange testa testb",
            "Illegal usage: `daterange` is mutually exclusive with arguments",
            2,
        ),
    ],
)
def test_mutually_exclusive_args(value: str, expected_output: str, expected_code: int):
    mutual_date_option = MutuallyExclusiveOption(
        mutually_exclusive=[("daterange", str), ("days", int)]
    )

    app = typer.Typer()

    @app.command()
    def some_fun(
        days: Annotated[
            int | None,
            typer.Option(
                "--days",
                "-d",
                click_type=mutual_date_option,
                help="integer # of days in the past, from today, to sync for",
            ),
        ] = None,
        daterange: Annotated[
            tuple[str, str] | None,
            typer.Option(
                "--daterange",
                "-r",
                click_type=mutual_date_option,
                help="""Two dates bounding inclusively the dates to sync for, separated by a space.
            No required order. If only one date is given, assumes bounds are from that date to today.""",
            ),
        ] = None,
    ):
        print("days-type: {}, daterange-type: {}".format(type(days), type(daterange)))
        print("days: {}, daterange: {}".format(days, daterange))
        return days, daterange

    result = runner.invoke(app, value)
    assert expected_code == result.exit_code
    assert expected_output in result.output


FAKE_TIME = datetime.datetime(2020, 12, 25, 17, 5, 55)


@pytest.fixture
def patch_datetime_today(monkeypatch):
    class mydatetime(datetime.datetime):
        @classmethod
        @override
        def today(cls):
            return FAKE_TIME

    monkeypatch.setattr(timesheetsync, "datetime", mydatetime)


@pytest.mark.parametrize(
    ("days", "daterange", "expected_start", "expected_end"),
    [
        (
            23,
            None,
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1)
            - datetime.timedelta(days=23 + 1),
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1),
        ),
        (
            None,
            ("2023-06-13", "2023-07-08"),
            datetime.datetime(2023, 6, 13, 0, 0, 0),
            datetime.datetime(2023, 7, 9, 0, 0, 0),
        ),
        (
            None,
            ["2019-06-13"],
            datetime.datetime(2019, 6, 13, 0, 0, 0),
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1),
        ),
        (
            None,
            None,
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            - datetime.timedelta(days=364),
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1),
        ),
        (
            23,
            ("2023-06-13", "2023-07-08"),
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1)
            - datetime.timedelta(days=23 + 1),
            FAKE_TIME.replace(hour=0, minute=0, second=0, microsecond=0)
            + datetime.timedelta(days=1),
        ),
    ],
)
def test_date_range_parsing(
    days, daterange, expected_start, expected_end, patch_datetime_today
):
    start_date, end_date = parse_date_range(
        days, (daterange and list(daterange)) or None
    )

    assert expected_start == start_date
    assert expected_end == end_date
