from toggl.TogglPy import Toggl
import argparse

def main(args):
    
    # create a Toggl object and set our API key
    toggl = Toggl()
    toggl.setAPIKey(args.toggl_key)

    response = toggl.request("https://www.toggl.com/api/v8/clients")

    # print the client name and id for each client in the response
    # list of returned values can be found in the Toggl docs:
    # https://github.com/toggl/toggl_api_docs/blob/master/chapters/clients.md
    for client in response:
        print("Client name: %s  Client id: %s" % (client['name'], client['id']))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('-tk', '--toggl-key', dest='toggl_key',
                        help='toggl api key')
    parser.add_argument('-hk', '--harvest-key', dest='harvest_key',
                        help='harvest api key')
    
    args = parser.parse_args()
    main(args)
