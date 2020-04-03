from toggl.TogglPy import Toggl, Endpoints
import argparse
import harvest
from datetime import datetime, date, timedelta, timezone
import pytz
import dateutil.parser

def main(args):

    
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
    
    toggl_entries = []
    for client in toggl.getClients():
        print("Client name: %s  Client id: %s" % (client['name'], client['id']))
        for workspace in toggl.getWorkspaces():
            print("workspace name: %s workspace id: %s" % (workspace['name'], workspace['id']))
            toggl_entries = toggl_entries + toggl.request(Endpoints.TIME_ENTRIES,
                                                          {'start_date': sdate_aw.isoformat(),
                                                           'end_date': edate_aw.isoformat()})
    
    account = harvest.Harvest(uri=args.harvest_url, account_id=args.harvest_account_id, personal_token=args.harvest_key)
    harvest_entries = []
    for client in account.clients():
        for project in account.projects_for_client(client['client']['id']):
            print('project')
            harvest_entries = harvest_entries + account.timesheets_for_project(project['project']['id'],
                                                                               start_date=sdate_aw.isoformat(),
                                                                               end_date=edate_aw.isoformat())
    
    delta = edate - sdate
    dates = [sdate + timedelta(days=i) for i in range(delta.days + 1)]
    toggl_entries_dict = {}
    for date in dates:
        from_toggl = [x for x in toggl_entries
                      if ((dateutil.parser.parse(x['start']).astimezone(toggl_tz) > toggl_tz.localize(date))
                          and (dateutil.parser.parse(x['start']).astimezone(toggl_tz) <= toggl_tz.localize(date) + timedelta(days=1)))]
        
        if from_toggl:
            toggl_entries_dict[date] = {
                'raw': from_toggl
            }
    
    print(toggl_entries_dict)
    print(sdate.isoformat())
    print(edate.isoformat())
    print('done')

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
