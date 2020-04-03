from toggl.TogglPy import Toggl, Endpoints
import argparse
import harvest
import datetime

def main(args):
    
    # create a Toggl object and set our API key
    toggl = Toggl()
    toggl.setAPIKey(args.toggl_key)
    toggl_entries = []
    for client in toggl.getClients():
        print("Client name: %s  Client id: %s" % (client['name'], client['id']))
        for workspace in toggl.getWorkspaces():
            print("workspace name: %s workspace id: %s" % (workspace['name'], workspace['id']))
            toggl_entries.append(toggl.request(Endpoints.TIME_ENTRIES, {'start_date': '2020-01-01T00:00:00+00:00', 'end_date': '2020-04-01T00:00:00+00:00'}))
            # for entry in toggl.request(Endpoints.TIME_ENTRIES, {'start_date': '2020-01-01T00:00:00+00:00', 'end_date': '2020-04-01T00:00:00+00:00'}):
            #     print("entry description: %s Task id: %s" % (entry['description'], entry['id']))
    
    account = harvest.Harvest("https://sirromsystems.harvestapp.com", account_id=args.harvest_account_id, personal_token=args.harvest_key)
    harvest_entries = []
    for client in account.clients():
        for project in account.projects_for_client(client['client']['id']):
            print('project')
            harvest_entries.append(account.timesheets_for_project(project['project']['id'], start_date='2020-01-01T00:00:00+00:00', end_date='2020-04-01T00:00:00+00:00'))
            # for timesheet in account.timesheets_for_project(project['project']['id'], start_date='2020-01-01T00:00:00+00:00', end_date='2020-04-01T00:00:00+00:00'):
            #     print('id %s notes %s spent_at %s hours %s' % (timesheet['day_entry']['id'], timesheet['day_entry']['notes'], timesheet['day_entry']['spent_at'], timesheet['day_entry']['hours']))
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-tk', '--toggl-key', dest='toggl_key',
                        help='toggl api key')
    parser.add_argument('-hai', '--harvest-account-id', dest='harvest_account_id',
                        help='harvest account id')
    parser.add_argument('-hk', '--harvest-key', dest='harvest_key',
                        help='harvest api key')
    
    args = parser.parse_args()
    main(args)
