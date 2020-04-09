# timesheet-sync
A tool to convert [Toggl](https://toggl.com/) time entries 
into [Harvest](https://www.getharvest.com/) timesheet entries.
## Prereqs
* Python 3
* Toggl account
* Harvest account

## Installation
    git clone git@github.com:a3ng7n/timesheet-sync.git
    cd timesheet-sync/
    pip install -r requirements.txt

## Usage
There are unfortunately quite a few args, but hey, what can you do:

    python timesheetsync.py ...
        -tk <toggl key>
        -url <harvest acct. url>
        -hai <harvest acct. no.>
        -hk <harvest key> 
        -htid <harvest task id>
        -hem <harvest user email>
        -hpid <harvest project id>>

For help, do the usual:

    python timesheetsync.py --help

## License
See LICENSE