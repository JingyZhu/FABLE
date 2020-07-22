"""
Check for false positive
"""
import json
import pymongo
from pymongo import MongoClient
from urllib.parse import urlsplit
import os, random
from collections import defaultdict
import time

import sys
sys.path.append('../../')
from ReorgPageFinder import discoverer, searcher, inferer, tools, ReorgPageFinder
import config
from utils import text_utils, url_utils, sic_transit

db = MongoClient(config.MONGO_HOSTNAME, username=config.MONGO_USER, password=config.MONGO_PWD, authSource='admin').ReorgPageFinder

all_urls = json.load(open('Broken_urls.json', 'r'))
sites = sorted(all_urls.keys())
count = 0

# rpf = ReorgPageFinder.ReorgPageFinder(logname='./fp.log')

# for site in sites:
#     for url in all_urls[site]:
#         count += 1
#         print(count, url)
#         reorg = db.reorg.find_one({'url': url})
#         if 'reorg_url_search' in reorg:
#             reorg_url = reorg['reorg_url_search']
#             check = rpf.fp_check(url, reorg_url)
#             if check:
#                 print('search false positive: ', reorg_url)
#                 db.na_urls.update_one({'_id': url}, {'$set': {
#                     "hostname": site,
#                     'false_positive_search': True
#                 }}, upsert=True)
#         if 'reorg_url_discover' in reorg:
#             reorg_url = reorg['reorg_url_discover']
#             check = rpf.fp_check(url, reorg_url)
#             if check:
#                 print('discover false positive: ', reorg_url)
#                 db.na_urls.update_one({'_id': url}, {'$set': {
#                     "hostname": site,
#                     'false_positive_discover': True
#                 }}, upsert=True)

url_site = {}
for site in sites:
    for url in all_urls[site]:
        url_site[url] = site

urls = list(url_site.keys())
random.shuffle(urls)
for count, url in enumerate(urls):
    print(count, url)
    broken = sic_transit.broken(url)[0]
    if not broken:
        print("Broken")
        db.na_urls.update_one({'_id': url}, {'$set':{
            'url': url,
            "hostname": url_site[url],
            "false_positive_broken": True
        }}, upsert=True)
        