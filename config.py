# DB hostname to connect
"""
config for global variable in this project
"""
import yaml
import os

MONGO_HOSTNAME='redwings.eecs.umich.edu'

LOCALSERVER_PORT=24680

# TODO: Make this initialization dynamic

if not os.path.exists(os.path.join(os.path.dirname(__file__), 'config.yml')):
    print("No config yaml file find")
else:
    config_yml = yaml.load(open(os.path.join(os.path.dirname(__file__), 'config.yml'), 'r'), Loader=yaml.FullLoader)
    if config_yml.get('proxies') is not None:
        PROXIES = [{'http': ip, 'https': ip } for ip in \
                    config_yml.get('proxies')]
    else: PROXIES = []
    PROXIES = PROXIES + [{}]  # One host do not have to use proxy
    HOSTS = config_yml.get('hosts')
    TMPPATH = config_yml.get('tmp_path')
    SEARCH_CX = config_yml.get('search_cx')
    SEARCH_KEY = config_yml.get('search_key')
    BING_SEARCH_KEY = config_yml.get('bing_search_key')
    MONGO_USER = config_yml.get('mongo_user')
    MONGO_PWD = config_yml.get('mongo_pwd')
    RPC_ADDRESS=config_yml.get('rpc_address')