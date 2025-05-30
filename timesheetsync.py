from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import pathlib
from typing import Annotated, Any, Literal, TypedDict, override
from datetime import datetime, timedelta
import click
from click.core import ParameterSource
from pydantic import AwareDatetime, BaseModel, BeforeValidator
import pytz
import dateutil.parser
import pprint
from tabulate import tabulate
import dateparser
import re
import requests
import textwrap
from toggl_python import ReportTimeEntry, SearchReportTimeEntriesResponse, Workspace
from toggl_python import BasicAuth, TokenAuth, auth as toggl_auth_
from toggl_python.entities import user as toggl_user
import typer

HARVEST_API_BASE_URL = "https://api.harvestapp.com/v2"
HARVEST_TIME_ENTRIES_URL = HARVEST_API_BASE_URL + "/time_entries"
HARVEST_USERS_URL = HARVEST_API_BASE_URL + "/users"
HARVEST_CLIENTS_URL = HARVEST_API_BASE_URL + "/clients"
HARVEST_TASKS_URL = HARVEST_API_BASE_URL + "/tasks"
HARVEST_TASK_ASSIGNMENTS_URL = HARVEST_API_BASE_URL + "/task_assignments"
HARVEST_PROJECTS_URL = HARVEST_API_BASE_URL + "/projects"


class MutuallyExclusiveOption(click.ParamType):
    mutually_exclusive: set[tuple[str, type]]
    exclusive_types: dict[str, type]
    exclusive_names: set[str]

    def __init__(
        self, *args, mutually_exclusive: list[tuple[str, type]] | None = None, **kwargs
    ):
        self.mutually_exclusive = set(mutually_exclusive or [])
        self.exclusive_types = {k: v for k, v in self.mutually_exclusive}
        self.exclusive_names = set(self.exclusive_types)
        super(MutuallyExclusiveOption, self).__init__()

    @override
    def get_metavar(self, param: click.Parameter) -> str | None:
        return ((name := param.name) is not None and name.upper()) or None

    @property
    @override
    def name(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        return ""

    @property
    def mutual_str(self) -> str:
        return ", ".join(self.exclusive_types)

    @override
    def convert(self, value, param, ctx):
        param_source = (
            ctx is not None
            and param is not None
            and param.name is not None
            and ctx.get_parameter_source(param.name)
        ) or None

        if (
            ctx is not None
            and self.exclusive_names.intersection(ctx.params)
            and param_source != ParameterSource.DEFAULT
        ):
            # if a value has affirmatively been provided to this option
            # and it's mutually exclusive with another provided option
            # fail out
            raise click.UsageError(
                "Illegal usage: `{}` is mutually exclusive with arguments `{}`.".format(
                    (param is not None and param.name) or "", self.mutual_str
                )
            )
        elif (
            ctx is not None
            and self.exclusive_names.intersection(ctx.params)
            and param_source == ParameterSource.DEFAULT
        ):
            # if this option has been defaulted and it's mutually exclusive
            # then null out its value
            value = None

        my_type = (
            param is not None
            and param.name is not None
            and self.exclusive_types.get(param.name, None)
        ) or None
        if my_type is not None:
            value = my_type(value)

        return super(MutuallyExclusiveOption, self).convert(value, param, ctx)


class Harvest:
    def __init__(self, hai: str, hk: str):
        self.account_id = hai
        self.auth_key = hk

    def post_all(self, url: str, data: dict[str, Any]):
        url_address = url
        headers = {
            "Authorization": "Bearer " + self.auth_key,
            "Harvest-Account-ID": self.account_id,
        }

        # find out total number of pages
        r = requests.post(url=url_address, headers=headers, data=data).json()

        return r

    def get_all(self, url: str, list_key: str | None = None):
        url_address = url
        headers = {
            "Authorization": "Bearer " + self.auth_key,
            "Harvest-Account-ID": self.account_id,
        }

        # find out total number of pages
        r_get = requests.get(url=url_address, headers=headers)
        r = r_get.json()
        total_pages = int(r["total_pages"])

        # results will be appended to this list
        all_results = []

        # loop through all pages and return JSON object
        for page in range(1, total_pages + 1):
            url = url_address + "?page=" + str(page)
            response = requests.get(url=url, headers=headers).json()
            all_results.append(response)

        if list_key is not None:
            data = []
            for page in all_results:
                data += page.get(list_key, [])

            return data
        else:
            return all_results

    def get_users(self):
        data = self.get_all(HARVEST_USERS_URL, "users")
        return data

    def get_time_entries(self):
        data = self.get_all(HARVEST_TIME_ENTRIES_URL, "time_entries")
        return data

    def get_clients(self):
        data = self.get_all(HARVEST_CLIENTS_URL, "clients")
        return data

    def get_task_assignments(self):
        data = self.get_all(HARVEST_TASK_ASSIGNMENTS_URL, "task_assignments")
        return data

    def get_tasks(self):
        data = self.get_all(HARVEST_TASKS_URL, "tasks")
        return data

    def get_projects(self):
        data = self.get_all(HARVEST_PROJECTS_URL, "projects")
        return data


@dataclass
class Credentials:
    toggl_key: str | None = None
    harvest_account_id: str | None = None
    harvest_key: str | None = None


@dataclass
class ConfirmedCredentials:
    toggl_key: str
    harvest_account_id: str
    harvest_key: str

    @classmethod
    def from_creds(cls, creds: Credentials):
        toggl_key = creds.toggl_key
        harvest_account_id = creds.harvest_account_id
        harvest_key = creds.harvest_key

        if (
            toggl_key is not None
            and harvest_account_id is not None
            and harvest_key is not None
        ):
            return cls(toggl_key, harvest_account_id, harvest_key)

        raise Exception("All credentials must be present.")


@dataclass
class State:
    cache_file: pathlib.Path = pathlib.Path(".creds")
    cache: bool = True
    store: bool = True
    toggl_auth: (
        toggl_auth_.BasicAuth | toggl_auth_.TokenAuth | Literal["test"] | None
    ) = None
    harvest_auth: Harvest | Literal["test"] | None = None


credentials = Credentials()
state = State()


@contextmanager
def update_cache(read: bool, store: bool, cache_file: pathlib.Path):
    if read:
        cache_file.touch(exist_ok=True)
        with open(cache_file, "r") as json_cache:
            cache_data: dict[str, Any] = (
                (cache_file.stat(follow_symlinks=True).st_size != 0)
                and json.load(json_cache)
            ) or {}
    else:
        cache_data = {}

    try:
        yield cache_data
    finally:
        if store:
            with open(cache_file, "w+") as json_cache:
                json.dump(cache_data, json_cache)


app = typer.Typer()

login_app = typer.Typer()


app.add_typer(login_app, name="login")

mutual_date_option = MutuallyExclusiveOption(
    mutually_exclusive=[("daterange", str), ("days", int)]
)


def parse_date_range(days: int | None, daterange: list[str] | None):
    if days is not None:
        edate = datetime.today().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        sdate = edate - timedelta(days=abs(days) + 1)
    elif daterange is not None:
        dates = [dateparser.parse(x) for x in daterange]
        dates = [x for x in dates if x is not None]

        if len(dates) < 1:
            raise ValueError(f"Unable to parse a date within range: {daterange}")

        if len(dates) < 2:
            dates.append(
                datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
            )

        dates = sorted(dates)
        sdate = dates[0]
        edate = dates[1] + timedelta(days=1)
    else:
        edate = datetime.today().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        sdate = edate + timedelta(days=-365)

    return sdate, edate


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    days: Annotated[
        int | None,
        typer.Option(
            "--days",
            "-d",
            click_type=mutual_date_option,
            help="""integer # of days in the past, from today, to sync for
            NOTE: This argument is mutually exclusive with arguments: [daterange, datebound].
            """,
        ),
    ] = None,
    daterange: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--daterange",
            "-dr",
            click_type=mutual_date_option,
            help="""Two dates bounding inclusively the dates to sync for, separated by a space.
            No required order. If only one date is given, assumes bounds are from that date to today.
            NOTE: This argument is mutually exclusive with arguments: [days, datebound].
            """,
        ),
    ] = None,
    datebound: Annotated[
        str | None,
        typer.Option(
            "--datebound",
            "-db",
            click_type=mutual_date_option,
            help="""A date in the past from which to include time entries.
            NOTE: This argument is mutually exclusive with arguments: [days, daterange].
            """,
        ),
    ] = None,
    cache_file: Annotated[
        pathlib.Path,
        typer.Option(
            help="Location to look for toggl and harvest credentials",
        ),
    ] = pathlib.Path(".creds"),
    cache: Annotated[
        bool,
        typer.Option(" /--no-cache", " /-n", help="Load credentials from file"),
    ] = True,
    store: Annotated[
        bool,
        typer.Option(" /--no-store", " /-s", help="Store credentials in file"),
    ] = True,
    test: Annotated[
        bool,
        typer.Option("--test/ ", "-t/ ", help="Test mode"),
    ] = False,
):
    if not ctx.invoked_subcommand:
        state.cache = cache
        state.cache_file = cache_file
        state.store = store

        toggl_auth = None
        harvest_auth = None

        start_date, end_date = parse_date_range(
            days, (daterange and list(daterange)) or (datebound and [datebound]) or None
        )

        with update_cache(cache, store, cache_file) as cache_data:
            credentials.toggl_key = cache_data.get("toggl_key", None)

            attempt = 0
            while toggl_auth is None:
                if attempt > 3:
                    raise typer.Exit(1)

                if credentials.toggl_key is None:
                    print("Please enter toggl login credentials...")
                    email = typer.prompt("Toggl email")
                    pw = typer.prompt("Toggl password", hide_input=True)
                    toggl_auth = (
                        toggl_login_test(email, pw) if test else toggl_login(email, pw)
                    )
                else:
                    toggl_auth = (
                        "test" if test else toggl_auth_.TokenAuth(credentials.toggl_key)
                    )

                attempt += 1

            print("toggl login complete")

            credentials.harvest_account_id = cache_data.get("harvest_account_id", None)
            credentials.harvest_key = cache_data.get("harvest_key", None)

            attempt = 0
            while harvest_auth is None:
                if (credentials.harvest_account_id is None) or (
                    credentials.harvest_key is None
                ):
                    if attempt > 3:
                        raise typer.Exit(1)
                    print("Please enter harvest login credentials...")
                    account_id = typer.prompt("Harvest account id")
                    key = typer.prompt("Harvest key")
                    harvest_auth = (
                        harvest_login_test(account_id, key)
                        if test
                        else harvest_login(account_id, key)
                    )
                else:
                    harvest_auth = (
                        "test"
                        if test
                        else Harvest(
                            credentials.harvest_account_id, credentials.harvest_key
                        )
                    )

                attempt += 1

            print("harvest login complete")

            creds = ConfirmedCredentials.from_creds(credentials)
            cache_data.update(asdict(creds))

        if toggl_auth != "test" and harvest_auth != "test":
            do_sync(
                toggl_auth,
                harvest_auth,
                start_date,
                end_date,
                "",
            )


def login_result_callback(*_args, **_kwargs):
    with update_cache(state.cache, state.store, state.cache_file) as cache_data:
        updated_credentials = {
            k: v for k, v in asdict(credentials).items() if v is not None
        }
        cache_data.update(updated_credentials)


@login_app.callback(result_callback=login_result_callback)
def login_callback(
    cache_file: Annotated[
        pathlib.Path,
        typer.Option(
            help="Location to look for toggl and harvest credentials",
        ),
    ] = pathlib.Path(".creds"),
    store: Annotated[
        bool,
        typer.Option(" /--no-store", " /-s", help="Store credentials in file"),
    ] = True,
):
    state.cache = True
    state.cache_file = cache_file
    state.store = store


@login_app.command("harvest")
def harvest_login(
    account_id: Annotated[str, typer.Option(prompt=True)],
    key: Annotated[str, typer.Option(prompt=True)],
):
    harvester = Harvest(account_id, key)
    print("harvest auth success")
    credentials.harvest_account_id = account_id
    credentials.harvest_key = key
    state.harvest_auth = harvester

    return harvester


@login_app.command("harvest-test")
def harvest_login_test(
    account_id: Annotated[str, typer.Option(prompt=True)],
    key: Annotated[str, typer.Option(prompt=True)],
):
    print("harvest auth success")
    credentials.harvest_account_id = account_id
    credentials.harvest_key = key
    state.harvest_auth = "test"

    return "test"


@login_app.command("toggl")
def toggl_login(
    email: Annotated[str, typer.Option(prompt=True, help="toggl login email")],
    password: Annotated[
        str, typer.Option(prompt=True, hide_input=True, help="toggl login password")
    ],
):
    auth = toggl_auth_.BasicAuth(username=email, password=password)
    user = toggl_user.CurrentUser(auth=auth).me()
    print("toggl auth success")
    credentials.toggl_key = user.api_token
    state.toggl_auth = auth

    return auth


@login_app.command("toggl-key")
def toggl_login_key(
    key: Annotated[str, typer.Option(prompt=True, help="toggl access key")],
):
    auth = toggl_auth_.TokenAuth(key)
    user = toggl_user.CurrentUser(auth=auth).me()
    print("toggl auth success")
    credentials.toggl_key = user.api_token
    state.toggl_auth = auth

    return auth


@login_app.command("toggl-test")
def toggl_login_test(
    email: Annotated[str, typer.Option(prompt=True, help="toggl login email")],
    password: Annotated[
        str, typer.Option(prompt=True, hide_input=True, help="toggl login password")
    ],
):
    print("toggl auth success")
    credentials.toggl_key = password
    state.toggl_auth = "test"
    return "test"


@app.command()
def sync(
    toggl_key: Annotated[str, typer.Option("--toggl-key", "-tk", help="toggl api key")],
    harvest_account_id: Annotated[
        str, typer.Option("--harvest-account-id", "-hai", help="harvest account id")
    ],
    harvest_key: Annotated[
        str, typer.Option("--harvest-key", "-hk", help="harvest api key")
    ],
    harvest_email: Annotated[
        str | None,
        typer.Option(
            "--harvest-email",
            "-hem",
            help="the email address associated with your harvest user to create new time entries under",
        ),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option(
            "--days",
            "-d",
            click_type=mutual_date_option,
            help="""integer # of days in the past, from today, to sync for
            NOTE: This argument is mutually exclusive with arguments: [daterange, datebound].
            """,
        ),
    ] = None,
    daterange: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--daterange",
            "-dr",
            click_type=mutual_date_option,
            help="""Two dates bounding inclusively the dates to sync for, separated by a space.
            No required order. If only one date is given, assumes bounds are from that date to today.
            NOTE: This argument is mutually exclusive with arguments: [days, datebound].
            """,
        ),
    ] = None,
    datebound: Annotated[
        str | None,
        typer.Option(
            "--datebound",
            "-db",
            click_type=mutual_date_option,
            help="""A date in the past from which to include time entries.
            NOTE: This argument is mutually exclusive with arguments: [days, daterange].
            """,
        ),
    ] = None,
):
    start_date, end_date = parse_date_range(
        days, (daterange and list(daterange)) or (datebound and [datebound]) or None
    )

    toggl = toggl_auth_.TokenAuth(toggl_key)
    harvest = harvest_login(harvest_account_id, harvest_key)

    do_sync(
        toggl,
        harvest,
        start_date,
        end_date,
        harvest_email,
    )


class TogglTimeEntry(BaseModel):
    project_id: Annotated[str, BeforeValidator(lambda v: str(v or -1))]
    description: Annotated[str, BeforeValidator(lambda v: v or "")]
    billable: bool
    billable_amount_in_cents: int | None
    currency: str
    hourly_rate_in_cents: int | None
    row_number: int
    tag_ids: list[int]
    task_id: int | None
    user_id: int
    username: str
    at: AwareDatetime
    at_tz: AwareDatetime
    id: int
    seconds: int
    start: AwareDatetime
    stop: AwareDatetime

    @classmethod
    def from_resp(cls, resp: SearchReportTimeEntriesResponse):
        assert len(resp.time_entries) == 1
        time_entry = resp.time_entries[0]
        combined = time_entry.model_dump()
        combined.update(resp.model_dump())
        new_model = cls.model_validate(combined)
        return new_model


class HarvestTimeEntry(TypedDict):
    id: int
    spent_date: str
    hours: float
    hours_without_timer: float
    rounded_hours: float
    notes: str
    is_locked: bool
    locked_reason: str
    is_closed: bool
    is_billed: bool


class RawTaskContext[T, U](TypedDict):
    raw: T
    tasks: dict[str, U]


class CombinedEntries(TypedDict):
    toggl: RawTaskContext[list[TogglTimeEntry], dict[str, float]]
    harvest: RawTaskContext[list[HarvestTimeEntry], float]


def do_sync(
    toggl: BasicAuth | TokenAuth,
    harvest: Harvest,
    start_date: datetime,
    end_date: datetime,
    harvest_email: str | None = None,
):
    """Convert Toggl time entries into Harvest timesheet entries."""
    pp = pprint.PrettyPrinter(indent=4)

    toggl_me = toggl_user.CurrentUser(toggl).me()
    toggl_tz_str = toggl_me.timezone
    toggl_tz = pytz.timezone(toggl_tz_str)

    # do some fancy date windowing required for retrieving tasks from toggl
    toggl_dateranges = []
    chunks = (end_date - start_date).days // 180
    partials = (end_date - start_date).days % 180

    for i in range((end_date - start_date).days // 180):
        toggl_dateranges.append(
            [
                start_date + timedelta(days=i * 180),
                start_date + timedelta(days=(i + 1) * 180 - 1),
            ]
        )

    if partials:
        toggl_dateranges.append(
            [
                start_date + timedelta(days=chunks * 180),
                start_date + timedelta(days=chunks * 180 + partials),
            ]
        )

    toggl_dateranges = [
        [toggl_tz.localize(dr[0]), toggl_tz.localize(dr[1])] for dr in toggl_dateranges
    ]

    # collect toggl entries
    toggl_entries = []
    time_reports = []

    toggl_workspaces = Workspace(toggl).list()

    for wid in [w.id for w in toggl_workspaces]:
        for dr in toggl_dateranges:
            more_reports = True
            page = 0
            while more_reports:
                reports = ReportTimeEntry(toggl).search(
                    wid, dr[0], dr[1], page_number=page
                )
                time_reports = reports + time_reports
                more_reports = len(reports) > 0
                page += 1

    toggl_entries = [TogglTimeEntry.from_resp(report) for report in time_reports]

    task_names: list[dict[str, str | int]] = [
        {
            "id": x.project_id + x.description,
            "pid": x.project_id,
            "description": x.description,
            "project": x.project_id,
        }
        for x in toggl_entries
    ]
    toggl_task_names = list({x["id"]: x for x in task_names}.values())
    toggl_task_names = sorted(
        toggl_task_names, key=lambda k: k["pid"] if k["pid"] else 0
    )
    for i, t in enumerate(toggl_task_names):
        t["id"] = i

    # collect harvest entries
    harvest_users = harvest.get_users()

    if not harvest_email:
        email_choices = click.Choice([x["email"] for x in harvest_users])
        harvest_email = typer.prompt(
            "Type the email address associated with the harvest account you'd like to sync to",
            show_choices=True,
            type=email_choices,
        )

    try:
        harvest_user_id = [
            usr["id"] for usr in harvest_users if usr["email"] == harvest_email
        ].pop()
    except IndexError:
        print("Could not find user with email address: {0}".format(harvest_email))
        raise

    harvest_entries = harvest.get_time_entries()
    if not isinstance(harvest_entries, list):
        raise RuntimeError(
            "Unexpected object type received when querying for harvest time entries"
        )
    harvest_entries: list[HarvestTimeEntry] = harvest_entries

    harvest_projects = harvest.get_projects()
    harvest_task_assignments = harvest.get_task_assignments()

    # organize the list of task assignments to be used for listing later
    for task_assignment in harvest_task_assignments:
        try:
            task_assignment["client"] = [
                project["client"]
                for project in harvest_projects
                if project["id"] == task_assignment["project"]["id"]
            ].pop()
        except IndexError:
            print(
                "Could not find project with id: {0}".format(
                    task_assignment["project"]["id"]
                )
            )
            raise

    harvest_task_names = sorted(
        harvest_task_assignments, key=lambda k: k["client"]["id"]
    )

    # organize toggl entries by dates worked
    delta = end_date - start_date
    dates = [start_date + timedelta(days=i) for i in range(delta.days + 1)]

    combined_entries_dict: dict[datetime, CombinedEntries] = {}

    for date in dates:
        # collect entries from either platform on the given date
        from_toggl = [
            x
            for x in toggl_entries
            if (
                (x.start.astimezone(toggl_tz) > toggl_tz.localize(date))
                and (
                    x.start.astimezone(toggl_tz)
                    <= toggl_tz.localize(date) + timedelta(days=1)
                )
            )
        ]

        from_harvest = [
            x
            for x in harvest_entries
            if dateutil.parser.parse(x["spent_date"]).astimezone(toggl_tz)
            == toggl_tz.localize(date)
        ]

        if from_toggl or from_harvest:
            combined_entries_dict[date] = {
                "toggl": {"raw": from_toggl, "tasks": {}},
                "harvest": {"raw": from_harvest, "tasks": {}},
            }

            # organize raw entries into unique tasks, and total time for that day
            for entry in combined_entries_dict[date]["toggl"]["raw"]:
                if (
                    entry.project_id
                    not in combined_entries_dict[date]["toggl"]["tasks"].keys()
                ):
                    combined_entries_dict[date]["toggl"]["tasks"][entry.project_id] = {}

                try:
                    combined_entries_dict[date]["toggl"]["tasks"][entry.project_id][
                        entry.description
                    ] += entry.seconds / 3600
                except KeyError:
                    combined_entries_dict[date]["toggl"]["tasks"][entry.project_id][
                        entry.description
                    ] = entry.seconds / 3600

            for entry in combined_entries_dict[date]["harvest"]["raw"]:
                try:
                    combined_entries_dict[date]["harvest"]["tasks"][entry["notes"]] += (
                        entry["hours"]
                    )
                except KeyError:
                    combined_entries_dict[date]["harvest"]["tasks"][entry["notes"]] = (
                        entry["hours"]
                    )

    # prompt the user for a task association config
    task_association = task_association_config(toggl_task_names, harvest_task_names)

    # add data to harvest
    add_to_harvest = []
    for date, entry in combined_entries_dict.items():
        if entry["toggl"]["tasks"] and not entry["harvest"]["tasks"]:
            for pid in entry["toggl"]["tasks"].keys():
                for task in entry["toggl"]["tasks"][pid].keys():
                    for hidpair in list(
                        zip(
                            task_association[pid][task]["harvest_project_id"],
                            task_association[pid][task]["harvest_task_id"],
                        )
                    ):
                        add_to_harvest.append(
                            {
                                "user_id": harvest_user_id,
                                "project_id": hidpair[0],
                                "task_id": hidpair[1],
                                "spent_date": date.date().isoformat(),
                                "hours": round(entry["toggl"]["tasks"][pid][task], 2),
                                "notes": task,
                            }
                        )

    print("The following Toggl entries will be added to Harvest:")
    add_to_harvest_tabulated = {
        k: [d[k] for d in add_to_harvest] for k in add_to_harvest[0]
    }
    print(tabulate(add_to_harvest_tabulated, headers="keys"))
    if input("""Add the entries noted above to harvest? (y/n)""").lower() in (
        "y",
        "yes",
    ):
        for entry in add_to_harvest:
            #'{"user_id":1782959,"project_id":14307913,"task_id":8083365,"spent_date":"2017-03-21","hours":1.0}'
            print("About to post: ")
            pp.pprint(entry)
            pp.pprint(harvest.post_all(url=HARVEST_TIME_ENTRIES_URL, data=entry))
    else:
        print("aborted")
        exit(1)

    print("done!")
    exit(0)


def presentation_table(toggl_tasks, harvest_tasks):
    presentation_header = [
        "Toggl #",
        "Toggl Project",
        "Toggl Task Desc.",
        "Harvest #",
        "Harvest Client",
        "Harvest Project",
        "Harvest Task",
    ]
    presentation_table = []
    while True:
        idx = len(presentation_table)
        if (idx < len(toggl_tasks)) and (
            idx < len(harvest_tasks)
        ):  # add both details to table
            line = [
                toggl_tasks[idx]["id"],
                textwrap.shorten(toggl_tasks[idx]["project"] or "", width=20),
                toggl_tasks[idx]["description"],
                idx,
                textwrap.shorten(harvest_tasks[idx]["client"]["name"] or "", width=20),
                harvest_tasks[idx]["project"]["name"],
                harvest_tasks[idx]["task"]["name"],
            ]
        elif idx < len(toggl_tasks):  # add toggl detail only to table
            line = [
                toggl_tasks[idx]["id"],
                textwrap.shorten(toggl_tasks[idx]["project"] or "", width=20),
                toggl_tasks[idx]["description"],
                None,
                None,
                None,
                None,
            ]
        elif idx < len(harvest_tasks):  # add harvest detail only to table
            line = [
                None,
                None,
                None,
                idx,
                textwrap.shorten(harvest_tasks[idx]["client"]["name"] or "", width=20),
                harvest_tasks[idx]["project"]["name"],
                harvest_tasks[idx]["task"]["name"],
            ]
        else:
            break
        presentation_table.append(line)

    return presentation_table, presentation_header


def task_association_config(toggl_tasks, harvest_tasks):
    print("""The following are two tables, one showing the tasks across your Toggl account, and the other showing
tasks across your Harvest account.""")
    print(tabulate(*presentation_table(toggl_tasks, harvest_tasks), tablefmt="grid"))

    help_msg = """You'll need to enter which Toggl tasks you'd like to associate with which Harvest tasks.
You'll be asked to enter an association formula - the formula should take the following form:

    <task config>|<task config>|..., where each <task config> is formatted as follows:
    (toggl ids)>(harvest ids), where ids can be comma separated and include :'s to denote lists.

For example:
User enters: 1:3,5,7>2,3|4,6>1
Result: Toggl entries in task #s 1,2,3,5 and 7 will be added as Harvest entries in task #s 2 and 3, and
        Toggl entries in task #s 4 and 6 will be added as Harvest entries in task #1
User enters: 1-3,5,7>2
Result: Toggl entires in task #s 1,2,3,5 and 7 will be added as Harvest entries in task #2 only

NOTE: Any task #s not appearing in a task config will be ignored."""

    print(help_msg)

    task_association = {}
    for task in toggl_tasks:
        if task["pid"] not in task_association.keys():
            task_association[task["pid"]] = {}

        if task["description"] not in task_association[task["pid"]].keys():
            task_association[task["pid"]][task["description"]] = {
                "harvest_project_id": [],
                "harvest_task_id": [],
            }

    cfgpat = re.compile(
        r"((?P<config>(?P<togglids>(((\d+\:\d+)|(\d+)){1}\,?)+)(\>{1})(?P<harvestids>(((\d+\:\d+)|(\d+)){1}\,?)+))(\|)?)"
    )
    idpat = re.compile(
        r"(?P<id>((?P<list>(?P<first>\d+)\:(?P<second>\d+))|(?P<single>\d+)){1}\,?)"
    )

    config_groups = []
    while True:
        task_config = input("Enter one or more task configs:")

        for cfgmatch in [m.groupdict() for m in cfgpat.finditer(task_config)]:
            config_group = {}

            tidmatches = [m.groupdict() for m in idpat.finditer(cfgmatch["togglids"])]
            hidmatches = [m.groupdict() for m in idpat.finditer(cfgmatch["harvestids"])]

            htasks = []
            for idmatch in hidmatches:
                if idmatch["list"] is not None:
                    htasks = (
                        htasks
                        + harvest_tasks[
                            int(idmatch["first"]) : int(idmatch["second"]) + 1
                        ]
                    )
                else:
                    htasks.append(harvest_tasks[int(idmatch["single"])])

            ttasks = []
            for idmatch in tidmatches:
                if idmatch["list"] is not None:
                    ttasks = (
                        ttasks
                        + toggl_tasks[
                            int(idmatch["first"]) : int(idmatch["second"]) + 1
                        ]
                    )
                else:
                    ttasks.append(toggl_tasks[int(idmatch["single"])])

            config_group["htasks"] = htasks
            config_group["ttasks"] = ttasks

            config_groups.append(config_group)

        ttasks_ignored = [
            t
            for t in toggl_tasks
            if t not in [x for grp in config_groups for x in grp["ttasks"]]
        ]
        htasks_ignored = [
            t
            for t in harvest_tasks
            if t not in [x for grp in config_groups for x in grp["htasks"]]
        ]

        print("""The following are the tasks that will be ignored - """)
        print(
            tabulate(
                *presentation_table(ttasks_ignored, htasks_ignored), tablefmt="grid"
            )
        )
        cont = input("""add another task config? (y/n)""")
        if cont.lower() not in ("y", "yes"):
            break

    for config_group in config_groups:
        for task in config_group["ttasks"]:
            task_association[task["pid"]][task["description"]][
                "harvest_project_id"
            ].append(*[h["project"]["id"] for h in config_group["htasks"]])
            task_association[task["pid"]][task["description"]][
                "harvest_task_id"
            ].append(*[h["task"]["id"] for h in config_group["htasks"]])

    return task_association


if __name__ == "__main__":
    app()
