from toggl.TogglPy import Toggl, Endpoints
import argparse
import harvest
from datetime import datetime, date, timedelta, timezone
import pytz
import dateutil.parser
import pprint

def main(args):
    pp = pprint.PrettyPrinter(indent=4)
    
    # create a Toggl object and set our API key
    toggl = Toggl()
    toggl.setAPIKey(args.toggl_key)
    
    toggl_tz_str = toggl.request("https://www.toggl.com/api/v8/me")['data']['timezone']
    print(toggl_tz_str)
    toggl_tz = pytz.timezone(toggl_tz_str)
    
    sdate = datetime(2020, 1, 1, 0, 0, 0, 0)
    sdate_aw = toggl_tz.localize(sdate)
    edate = datetime.today().replace(microsecond=0)
    edate_aw = toggl_tz.localize(edate)
    
    # collect toggl entries
    toggl_entries = []
    for client in toggl.getClients():
        print("Client name: %s  Client id: %s" % (client['name'], client['id']))
        for workspace in toggl.getWorkspaces():
            print("workspace name: %s workspace id: %s" % (workspace['name'], workspace['id']))
            toggl_entries = toggl_entries + toggl.request(Endpoints.TIME_ENTRIES,
                                                          {'start_date': sdate_aw.isoformat(),
                                                           'end_date': edate_aw.isoformat()})
    
    # collect harvest entries
    account = harvest.Harvest(uri=args.harvest_url, account_id=args.harvest_account_id, personal_token=args.harvest_key)
    harvest_entries = []
    for client in account.clients():
        for project in account.projects_for_client(client['client']['id']):
            print('project')
            harvest_entries = harvest_entries + account.timesheets_for_project(project['project']['id'],
                                                                               start_date=sdate_aw.isoformat(),
                                                                               end_date=edate_aw.isoformat())
    
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
    
    
    pp.pprint([[combined_entries_dict[x][y]['tasks'] for y in combined_entries_dict[x].keys()] for x in combined_entries_dict.keys()])

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
    
    
    args = parser.parse_args()
    main(args)
