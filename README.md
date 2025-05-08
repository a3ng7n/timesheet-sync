# timesheet-sync

A tool to convert [Toggl](https://toggl.com/) time entries
into [Harvest](https://www.getharvest.com/) timesheet entries.

## Requirements

Primarily this tool is meant for people who use Toggl to track their time spent on a task, but also want to use Harvest to track budgets and handle invoicing etc..

So, you'll need the following:

- Python 3.12
- Toggl account
- Harvest account

You'll also need to obtain api keys for either account:

- Toggl api key: <https://toggl.com/app/profile> (and see "API token" at the bottom of the page)
- Harvest api key: <https://id.getharvest.com/oauth2/access_tokens/new>

## Installation

    git clone git@github.com:a3ng7n/timesheet-sync.git
    cd timesheet-sync/
    uv sync

## Usage

There are unfortunately quite a few args, but hey, what can you do:

    python timesheetsync.py [-h] [-tk TOGGL_KEY] [-url HARVEST_URL]
                        [-hai HARVEST_ACCOUNT_ID] [-hk HARVEST_KEY]
                        [-hem HARVEST_EMAIL]
                        [-d DAYS | -dr DATERANGE [DATERANGE ...]]

    optional arguments:
      -h, --help            show this help message and exit
      -tk TOGGL_KEY, --toggl-key TOGGL_KEY
                            toggl api key
      -url HARVEST_URL, --harvest-url HARVEST_URL
                            harvest url
      -hai HARVEST_ACCOUNT_ID, --harvest-account-id HARVEST_ACCOUNT_ID
                            harvest account id
      -hk HARVEST_KEY, --harvest-key HARVEST_KEY
                            harvest api key
      -hem HARVEST_EMAIL, --harvest-email HARVEST_EMAIL
                            the email address associated with your harvest user to
                            create new time entries under

      Time bounds of syncronization. If neither are given, assumes 365 days in
      the past to today.

      -d DAYS, --days DAYS  integer # of days in the past, from today, to sync for
      -dr DATERANGE [DATERANGE ...], --daterange DATERANGE [DATERANGE ...]
                            Two dates bounding inclusively the dates to sync for,
                            separated by a space. No required order. If only one
                            date is given, assumes bounds are from that date to
                            today.

For help, do the usual:

    python timesheetsync.py --help

## License

See LICENSE
