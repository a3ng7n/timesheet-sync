from toggl.TogglPy import Toggl, Endpoints
import argparse
import harvest
from datetime import datetime, timedelta
import pytz
import dateutil.parser
import pprint
from tabulate import tabulate
import dateparser

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

    sdate_aw = toggl_tz.localize(sdate)
    edate_aw = toggl_tz.localize(edate)

    toggl_dateranges = []
    chunks = (edate - sdate).days // 180
    partials = (edate - sdate).days % 180

    for i in range((edate - sdate).days // 180):
        toggl_dateranges.append([sdate + timedelta(days=i * 180), sdate + timedelta(days=(i + 1) * 180)])

    if partials:
        toggl_dateranges.append([sdate + timedelta(days=chunks * 180), sdate + timedelta(days=chunks * 180 + partials)])

    toggl_dateranges = [[toggl_tz.localize(dr[0]), toggl_tz.localize(dr[1])] for dr in toggl_dateranges]
    
    # collect toggl entries
    toggl_entries = []
    toggl_clients = toggl_account.getClients()
    toggl_task_combinations = {}
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
                    {'workspace_id': wid, 'user_agent': 'jamesbond', 'page': page, 'since': dr[0], 'until': dr[1]})
                toggl_entries = entries['data'] + toggl_entries
                entries_left = entries['total_count'] - entries['per_page'] if page == 1 else entries_left - entries['per_page']
                page += 1
    
    task_names = [{'id': str(x['pid']) + x['description'], 'pid': x['pid'], 'description': x['description']} for x in toggl_entries]
    toggl_task_names = list({x['id']:x for x in task_names}.values())
    for i, t in enumerate(toggl_task_names):
        t['id'] = i
    # toggl_task_combinations.append(
    #     [[len(toggl_task_combinations), client['name'], workspace['name'], task] for task in task_names])
    # print(tabulate(toggl_task_combinations, ['#', 'Client Name', 'Workspace Name'], tablefmt="grid"))
                
    # collect harvest entries
    harvest_account = harvest.Harvest(uri=args.harvest_url, account_id=args.harvest_account_id, personal_token=args.harvest_key)
        
    try:
        harvest_user_id = [x['user']['id'] for x in harvest_account.users if x['user']['email'] == args.harvest_email].pop()
    except IndexError:
        print("Could not find user with email address: {0}".format(args.harvest_email))
        raise
    
    harvest_entries = []
    harvest_clients = harvest_account.clients()
    for client in harvest_clients:
        harvest_projects = harvest_account.projects_for_client(client['client']['id'])
        for project in harvest_projects:
            
            harvest_entries = harvest_entries + harvest_account.timesheets_for_project(project['project']['id'],
                                                                               start_date=sdate_aw.isoformat(),
                                                                               end_date=edate_aw.isoformat())
    for e in harvest_entries:
        e['day_entry']['client_id'] = [y['project']['client_id'] for y in harvest_projects if
                      y['project']['id'] == e['day_entry']['project_id']].pop()
    
    task_names = [{'id': str(x['day_entry']['client_id'])\
                         + str(x['day_entry']['project_id'])\
                         + str(x['day_entry']['task_id']),
                   'client_id': x['day_entry']['client_id'],
                   'project_id': x['day_entry']['project_id'],
                   'task_id': x['day_entry']['task_id']}
                  for x in harvest_entries]
    harvest_task_names = list({x['id']: x for x in task_names}.values())
    for i, t in enumerate(harvest_task_names):
        t['id'] = i
    
    task_config = task_allocation_config(toggl_account, toggl_task_names, harvest_account, harvest_task_names)
    
    
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

        from_harvest = [x['day_entry'] for x in harvest_entries
                      if dateutil.parser.parse(x['day_entry']['spent_at']).astimezone(toggl_tz) == toggl_tz.localize(date)]
        
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
                        try:
                            combined_entries_dict[date][platform]['tasks'][entry['description']] += entry['duration']/3600
                        except KeyError:
                            combined_entries_dict[date][platform]['tasks'][entry['description']] = entry['duration']/3600
                    else:
                        try:
                            combined_entries_dict[date][platform]['tasks'][entry['notes']] += entry['hours']
                        except KeyError:
                            combined_entries_dict[date][platform]['tasks'][entry['notes']] = entry['hours']
            
    for date, entry in combined_entries_dict.items():
        if entry['toggl']['tasks'] and not entry['harvest']['tasks']:
            for task in entry['toggl']['tasks'].keys():
                data_for_entry = {'project_id': args.harvest_project_id,
                                  'task_id': args.harvest_task_id,
                                  'spent_at': date.date().isoformat(),
                                  'hours': round(entry['toggl']['tasks'][task],2),
                                  'notes': task}
                #pp.pprint(account.add_for_user(user_id=harvest_user_id, data=data_for_entry))

def task_allocation_config(toggl_object, toggl_tasks, harvest_object, harvest_tasks):
    print("TODO - show user which tasks were found in toggl, and which were found in harvest - then ask how to allocate them")

    print(tabulate(harvest_task_combinations, ['#', 'Client ID', 'Project ID', 'Task ID'], tablefmt="grid"))
    
    # task_names = [{'id': str(x['pid']) + x['description'], 'pid': x['pid'], 'description': x['description']} for x in
    #               toggl_entries]
    #
    # task_names = [{'id': str(x['day_entry']['client_id']) \
    #                      + str(x['day_entry']['project_id']) \
    #                      + str(x['day_entry']['task_id']),
    #                'client_id': x['day_entry']['client_id'],
    #                'project_id': x['day_entry']['project_id'],
    #                'task_id': x['day_entry']['task_id']}
    #               for x in harvest_entries]
    
    help_msg = """Format of the input should be as follows: <toggl ids>:<harvest ids>
        For example:
        User enters: 1-3,5,7:2-6
        Result: any tasks in toggl described by ids 1,2,3,5 and 7 will be added to harvest with project id and task
        id described by ids 2,3,4,5 and 6
        User enters: 1-3,5,7:2
        Result: any tasks in toggl described by ids 1,2,3,5 and 7 will be added to harvest with project id and task
        id described by id 2 only"""
    
    print(" TOGGL TASK TABLE --------------------- HARVEST TASK TABLE ")
    
    # task_association = {
    #     toggl_pid: {
    #         toggl_description: {
    #             harvest_project_id: 1234,
    #             harvest_task_id: 1234
    #     }
    # }
    return True
    
def show_preview():
    print("TODO - show the user a preview of what will be transferred over to harvest - perhaps also allow item by item approval")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-tk', '--toggl-key', dest='toggl_key',
                        help='toggl api key')
    parser.add_argument('-url', '--harvest-url', dest='harvest_url',
                        help='harvest url')
    parser.add_argument('-hai', '--harvest-account-id', dest='harvest_account_id',
                        help='harvest account id')
    parser.add_argument('-hk', '--harvest-key', dest='harvest_key',
                        help='harvest api key')
    parser.add_argument('-htid', '--harvest-task-id', dest='harvest_task_id', type=int,
                        help='task id to create new time entries under')
    parser.add_argument('-hpid', '--harvest-project-id', dest='harvest_project_id', type=int,
                        help='project id to create new time entries under')
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
