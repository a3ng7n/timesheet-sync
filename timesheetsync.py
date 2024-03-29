from toggl.TogglPy import Toggl, Endpoints
import argparse
from datetime import datetime, timedelta
import pytz
import dateutil.parser
import pprint
from tabulate import tabulate
import dateparser
import re
import requests
import textwrap

HARVEST_API_BASE_URL = "https://api.harvestapp.com/v2"
HARVEST_TIME_ENTRIES_URL = HARVEST_API_BASE_URL + "/time_entries"
HARVEST_USERS_URL = HARVEST_API_BASE_URL + "/users"
HARVEST_CLIENTS_URL = HARVEST_API_BASE_URL + "/clients"
HARVEST_TASKS_URL = HARVEST_API_BASE_URL + "/tasks"
HARVEST_TASK_ASSIGNMENTS_URL = HARVEST_API_BASE_URL + "/task_assignments"
HARVEST_PROJECTS_URL = HARVEST_API_BASE_URL + "/projects"

def main(args):
    pp = pprint.PrettyPrinter(indent=4)
    
    # create a Toggl object and set our API key
    toggl_account = Toggl()
    toggl_account.setAPIKey(args.toggl_key)
    
    toggl_tz_str = toggl_account.request("https://www.toggl.com/api/v8/me")['data']['timezone']
    toggl_tz = pytz.timezone(toggl_tz_str)
    
    # figure out what ranges to sync for
    if args.days:
        edate = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        sdate = edate - timedelta(days=abs(args.days) + 1)
    elif args.daterange:
        dates = [dateparser.parse(x) for x in args.daterange]
        if len(dates) < 2:
            dates.append(datetime.today().replace(hour=0, minute=0, second=0, microsecond=0))
        dates = sorted(dates)
        sdate = dates[0]
        edate = dates[1] + timedelta(days=1)
    else:
        edate = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        sdate = edate + timedelta(days=-365)
    
    # do some fancy date windowing required for retrieving tasks from toggl
    toggl_dateranges = []
    chunks = (edate - sdate).days // 180
    partials = (edate - sdate).days % 180

    for i in range((edate - sdate).days // 180):
        toggl_dateranges.append([sdate + timedelta(days=i * 180), sdate + timedelta(days=(i + 1) * 180 - 1)])

    if partials:
        toggl_dateranges.append([sdate + timedelta(days=chunks * 180), sdate + timedelta(days=chunks * 180 + partials)])

    toggl_dateranges = [[toggl_tz.localize(dr[0]), toggl_tz.localize(dr[1])] for dr in toggl_dateranges]
    
    # collect toggl entries
    toggl_entries = []
    toggl_clients = toggl_account.getClients()
    toggl_workspaces = toggl_account.getWorkspaces()
    toggl_projects = []
    for client in toggl_clients:
        toggl_projects = toggl_projects + toggl_account.getClientProjects(client['id'])
    
    for wid in [w['id'] for w in toggl_workspaces]:
        for dr in toggl_dateranges:
            entries_left = 1
            page = 1
            while entries_left > 0:
                entries = toggl_account.request(
                    Endpoints.REPORT_DETAILED,
                    {'workspace_id': wid, 'user_agent': 'https://github.com/a3ng7n/timesheet-sync', 'page': page, 'since': dr[0], 'until': dr[1]})
                toggl_entries = entries['data'] + toggl_entries
                entries_left = entries['total_count'] - entries['per_page'] if page == 1 else entries_left - entries['per_page']
                page += 1
    
    task_names = [{'id': str(x['pid']) + x['description'], 'pid': x['pid'], 'description': x['description'], 'project': x['project']} for x in toggl_entries]
    toggl_task_names = list({x['id']:x for x in task_names}.values())
    toggl_task_names = sorted(toggl_task_names, key=lambda k: k['pid'] if k['pid'] else 0)
    for i, t in enumerate(toggl_task_names):
        t['id'] = i
                
    # collect harvest entries
    harvester = Harvest(args.harvest_account_id, args.harvest_key)
    harvest_users = harvester.get_users()

    try:
        harvest_user_id = [usr['id'] for usr in harvest_users if usr['email'] == args.harvest_email].pop()
    except IndexError:
        print("Could not find user with email address: {0}".format(args.harvest_email))
        raise
    
    harvest_entries = harvester.get_time_entries()
    harvest_projects = harvester.get_projects()
    harvest_task_assignments = harvester.get_task_assignments()

    # organize the list of task assignments to be used for listing later
    for task_assignment in harvest_task_assignments:
        try:
            task_assignment['client'] = [project['client'] for project in harvest_projects if project['id'] == task_assignment['project']['id']].pop()
        except IndexError:
            print("Could not find project with id: {0}".format(task_assignment['project']['id']))
            raise

    harvest_task_names = sorted(harvest_task_assignments, key=lambda k: k['client']['id'])

    # prompt the user for a task association config
    task_association = task_association_config(toggl_task_names, harvest_task_names)
    
    # organize toggl entries by dates worked
    delta = edate - sdate
    dates = [sdate + timedelta(days=i) for i in range(delta.days + 1)]
    combined_entries_dict = {}
    for date in dates:
        # collect entries from either platform on the given date
        from_toggl = [x for x in toggl_entries
                      if ((dateutil.parser.parse(x['start']).astimezone(toggl_tz) > toggl_tz.localize(date))
                          and (dateutil.parser.parse(x['start']).astimezone(toggl_tz) <= toggl_tz.localize(date)
                               + timedelta(days=1)))]

        from_harvest = [x for x in harvest_entries
                      if dateutil.parser.parse(x['spent_date']).astimezone(toggl_tz) == toggl_tz.localize(date)]
        
        if from_toggl or from_harvest:
            combined_entries_dict[date] = {
                'toggl': {
                    'raw': from_toggl,
                    'tasks': {}
                },
                'harvest': {
                    'raw': from_harvest,
                    'tasks': {}
                }
            }
            
            # organize raw entries into unique tasks, and total time for that day
            for platform in combined_entries_dict[date].keys():
                for entry in combined_entries_dict[date][platform]['raw']:
                    if platform == 'toggl':
                        if entry['pid'] not in combined_entries_dict[date][platform]['tasks'].keys():
                            combined_entries_dict[date][platform]['tasks'][entry['pid']] = {}
                        
                        try:
                            combined_entries_dict[date][platform]['tasks'][entry['pid']][entry['description']] += entry['dur']/3600000
                        except KeyError:
                            combined_entries_dict[date][platform]['tasks'][entry['pid']][entry['description']] = entry['dur']/3600000
                    else:
                        try:
                            combined_entries_dict[date][platform]['tasks'][entry['notes']] += entry['hours']
                        except KeyError:
                            combined_entries_dict[date][platform]['tasks'][entry['notes']] = entry['hours']
    
    # add data to harvest
    add_to_harvest = []
    for date, entry in combined_entries_dict.items():
        if entry['toggl']['tasks'] and not entry['harvest']['tasks']:
            for pid in entry['toggl']['tasks'].keys():
                for task in entry['toggl']['tasks'][pid].keys():
                    for hidpair in list(zip(task_association[pid][task]['harvest_project_id'], task_association[pid][task]['harvest_task_id'])):
                        add_to_harvest.append({'user_id': harvest_user_id,
                                            'project_id': hidpair[0],
                                            'task_id': hidpair[1],
                                            'spent_date': date.date().isoformat(),
                                            'hours': round(entry['toggl']['tasks'][pid][task],2),
                                            'notes': task})
    
    print("The following Toggl entries will be added to Harvest:")
    add_to_harvest_tabulated = {k: [d[k] for d in add_to_harvest] for k in add_to_harvest[0]}
    print(tabulate(add_to_harvest_tabulated, headers='keys'))
    if input("""Add the entries noted above to harvest? (y/n)""").lower() in ('y', 'yes'):
        for entry in add_to_harvest:
            #'{"user_id":1782959,"project_id":14307913,"task_id":8083365,"spent_date":"2017-03-21","hours":1.0}'
            print('About to post: ')
            pp.pprint(entry)
            pp.pprint(harvester.post_all(url=HARVEST_TIME_ENTRIES_URL, data=entry))
    else:
        print('aborted')
        exit(1)
    
    print('done!')
    exit(0)

class Harvest:
    def __init__(self, hai: str, hk: str):
        self.account_id = hai
        self.auth_key = hk
    
    def post_all(self, url: str, data: dict):
        url_address = url
        headers = {
            "Authorization": "Bearer " + self.auth_key,
            "Harvest-Account-ID": self.account_id
        }

        # find out total number of pages
        r = requests.post(url=url_address, headers=headers, data=data).json()

        return r
    
    def get_all(self, url: str, list_key: str = None):

        url_address = url
        headers = {
            "Authorization": "Bearer " + self.auth_key,
            "Harvest-Account-ID": self.account_id
        }

        # find out total number of pages
        r = requests.get(url=url_address, headers=headers).json()
        total_pages = int(r['total_pages'])

        # results will be appended to this list
        all_results = []

        # loop through all pages and return JSON object
        for page in range(1, total_pages+1):

            url = url_address + "?page="+str(page)
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
        data = self.get_all(HARVEST_USERS_URL, 'users')
        return data

    def get_time_entries(self):
        data = self.get_all(HARVEST_TIME_ENTRIES_URL, 'time_entries')
        return data
    
    def get_clients(self):
        data = self.get_all(HARVEST_CLIENTS_URL, 'clients')
        return data
    
    def get_task_assignments(self):
        data = self.get_all(HARVEST_TASK_ASSIGNMENTS_URL, 'task_assignments')
        return data
    
    def get_tasks(self):
        data = self.get_all(HARVEST_TASKS_URL, 'tasks')
        return data
    
    def get_projects(self):
        data = self.get_all(HARVEST_PROJECTS_URL, 'projects')
        return data


def presentation_table(toggl_tasks, harvest_tasks):
    presentation_header = ['Toggl #', 'Toggl Project', 'Toggl Task Desc.',
                           'Harvest #', 'Harvest Client', 'Harvest Project', 'Harvest Task']
    presentation_table = []
    while True:
        idx = len(presentation_table)
        if (idx < len(toggl_tasks)) and (idx < len(harvest_tasks)): # add both details to table
            line = [toggl_tasks[idx]['id'],
                    textwrap.shorten(toggl_tasks[idx]['project'] or '', width=20),
                    toggl_tasks[idx]['description'],
                    idx,
                    textwrap.shorten(harvest_tasks[idx]['client']['name'] or '', width=20),
                    harvest_tasks[idx]['project']['name'],
                    harvest_tasks[idx]['task']['name']]
        elif idx < len(toggl_tasks):    # add toggl detail only to table
            line = [toggl_tasks[idx]['id'],
                    textwrap.shorten(toggl_tasks[idx]['project'] or '', width=20),
                    toggl_tasks[idx]['description'],
                    None,
                    None,
                    None,
                    None]
        elif idx < len(harvest_tasks):  # add harvest detail only to table
            line = [None,
                    None,
                    None,
                    idx,
                    textwrap.shorten(harvest_tasks[idx]['client']['name'] or '', width=20),
                    harvest_tasks[idx]['project']['name'],
                    harvest_tasks[idx]['task']['name']]
        else:
            break
        presentation_table.append(line)
    
    return presentation_table, presentation_header
    
def task_association_config(toggl_tasks, harvest_tasks):
    
    print("""The following are two tables, one showing the tasks across your Toggl account, and the other showing
tasks across your Harvest account.""")
    print(tabulate(*presentation_table(toggl_tasks, harvest_tasks), tablefmt="grid"))
    
    # task_names = [{'id': index, 'pid': x['pid'], 'description': x['description']}
    #               for x in toggl_entries]
    #
    # task_names = [{'id': index,
    #                'client_id': x['day_entry']['client_id'],
    #                'project_id': x['day_entry']['project_id'],
    #                'task_id': x['day_entry']['task_id']}
    #               for x in harvest_entries]
    # task_association = {
    #     toggl_pid: { toggl_description: {
    #                   harvest_project_id: 1234,
    #                   harvest_task_id: 1234 }
    # }
    
    help_msg ="""You'll need to enter which Toggl tasks you'd like to associate with which Harvest tasks.
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
        if task['pid'] not in task_association.keys():
            task_association[task['pid']] = { }

        if task['description'] not in task_association[task['pid']].keys():
            task_association[task['pid']][task['description']] = { 'harvest_project_id': [], 'harvest_task_id': [] }

    cfgpat = re.compile(
        r'((?P<config>(?P<togglids>(((\d+\:\d+)|(\d+)){1}\,?)+)(\>{1})(?P<harvestids>(((\d+\:\d+)|(\d+)){1}\,?)+))(\|)?)')
    idpat = re.compile(r'(?P<id>((?P<list>(?P<first>\d+)\:(?P<second>\d+))|(?P<single>\d+)){1}\,?)')
    
    config_groups = []
    while True:
        task_config = input("Enter one or more task configs:")
        
        for cfgmatch in [m.groupdict() for m in cfgpat.finditer(task_config)]:
            
            config_group = {}
            
            tidmatches = [m.groupdict() for m in idpat.finditer(cfgmatch['togglids'])]
            hidmatches = [m.groupdict() for m in idpat.finditer(cfgmatch['harvestids'])]
            
            htasks = []
            for idmatch in hidmatches:
                if idmatch['list'] is not None:
                    htasks = htasks + harvest_tasks[int(idmatch['first']):int(idmatch['second'])+1]
                else:
                    htasks.append(harvest_tasks[int(idmatch['single'])])

            ttasks = []
            for idmatch in tidmatches:
                if idmatch['list'] is not None:
                    ttasks = ttasks + toggl_tasks[int(idmatch['first']):int(idmatch['second'])+1]
                else:
                    ttasks.append(toggl_tasks[int(idmatch['single'])])
            
            config_group['htasks'] = htasks
            config_group['ttasks'] = ttasks
            
            config_groups.append(config_group)
        
        ttasks_ignored = [t for t in toggl_tasks if t not in [x for grp in config_groups for x in grp['ttasks']]]
        htasks_ignored = [t for t in harvest_tasks if t not in [x for grp in config_groups for x in grp['htasks']]]

        print("""The following are the tasks that will be ignored - """)
        print(tabulate(*presentation_table(ttasks_ignored, htasks_ignored), tablefmt="grid"))
        cont = input("""add another task config? (y/n)""")
        if cont.lower() not in ('y', 'yes'):
            break
    
    for config_group in config_groups:
        for task in config_group['ttasks']:
            task_association[task['pid']][task['description']]['harvest_project_id'].append(*[h['project']['id'] for h in
                                                                                            config_group['htasks']])
            task_association[task['pid']][task['description']]['harvest_task_id'].append(*[h['task']['id'] for h in
                                                                                          config_group['htasks']])

    return task_association

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='A tool to convert Toggl time entries into Harvest timesheet entries.')
    parser.add_argument('-tk', '--toggl-key', dest='toggl_key',
                        help='toggl api key')
    parser.add_argument('-url', '--harvest-url', dest='harvest_url',
                        help='harvest url')
    parser.add_argument('-hai', '--harvest-account-id', dest='harvest_account_id',
                        help='harvest account id')
    parser.add_argument('-hk', '--harvest-key', dest='harvest_key',
                        help='harvest api key')
    parser.add_argument('-hem', '--harvest-email', dest='harvest_email',
                        help='the email address associated with your harvest user to create new time entries under')
    timeparse = parser.add_argument_group(description='''Time bounds of syncronization. If neither are given, '''
                                          '''assumes 365 days in the past to today.''')
    mxg = timeparse.add_mutually_exclusive_group()
    mxg.add_argument('-d', '--days', dest='days', type=int,
                        help='integer # of days in the past, from today, to sync for')
    mxg.add_argument('-dr', '--daterange', dest='daterange', nargs='+',
                        help='''Two dates bounding inclusively the dates to sync for, separated by a space. '''
                             '''No required order. If only one date is given, assumes bounds are from that date to today.''')
    
    args = parser.parse_args()
    
    main(args)
