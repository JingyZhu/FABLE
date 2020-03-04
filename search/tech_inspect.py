"""
Use wappalyzer to inspect the technologies used by websites
"""
import sys
import requests
import os
import json
from pymongo import MongoClient
import random
import multiprocessing as mp
import pymongo
from collections import defaultdict
import re

sys.path.append('../')
import config
from utils import crawl, url_utils

db = MongoClient(config.MONGO_HOSTNAME).web_decay
PS = crawl.ProxySelector(config.PROXIES)

def dict_diff(a, b):
    """Return diff of dict a and b. Assum value of a and b are lists"""
    add_a, add_b = defaultdict(list), defaultdict(list)
    for typee, value in a.items():
        if typee not in b: add_a[typee] = value
        else:
            delta = set(value) - set(b[typee])
            if len(delta) > 0:
                add_a[typee] = list(delta)
    for typee, value in b.items():
        if typee not in a: add_b[typee] = value
        else:
            delta = set(value) - set(a[typee])
            if len(delta) > 0:
                add_b[typee] = list(delta)
    return add_a, add_b


def intersect_dict(a, b):
    """Return intersect of dict a and b. Assum value of a and b are lists"""
    intersect = {}
    for typee, value in a.items():
        if typee not in b: continue
        its = set(value).intersection(set(b[typee]))
        if len(its) > 0:
            intersect[typee] = list(its)
    return intersect


def crawl_analyze_sanity():
    """
    Crawl wayback and realweb of db.wappalyzer_sanity, and update dict into collection
    """
    urls = db.wappalyzer_sanity.find({"tech": {"$exists": False}})
    urls = list(urls)
    print("total:", len(urls))
    for i, obj in enumerate(urls):
        url = obj['_id']
        print(i, url)
        try:
            if 'web.archive.org' in url: tech = crawl.wappalyzer_analyze(url, proxy=PS.select_url())
            else: tech = crawl.wappalyzer_analyze(url, proxy=PS.select_url())
        except Exception as e:
            print(str(e))
            continue
        db.wappalyzer_sanity.update_one({"_id": url}, {"$set": {"tech": tech}})


def crawl_analyze_reorg():
    """
    Crawl wayback and realweb (copies) of db.wappalyzer_reorg, and update dict into collection
    """
    urls = db.wappalyzer_reorg.find({"tech": {"$exists": False}})
    urls = list(urls)
    print("total:", len(urls))
    for i, obj in enumerate(urls):
        url = obj['_id']
        print(i, url)
        try:
            if 'web.archive.org' in url: tech = crawl.wappalyzer_analyze(url, proxy=PS.select_url())
            else: tech = crawl.wappalyzer_analyze(url, proxy=PS.select_url())
        except:
            continue
        db.wappalyzer_reorg.update_one({"_id": url}, {"$set": {"tech": tech}})


def take_differences_intersect_sanity():
    sample = []
    urls = db.wappalyzer_sanity.aggregate([
        {"$match": {"tech": {"$exists": True}}},
        {"$group": {"_id": "$url",  "total": {"$sum":1}, "techs": {"$push":{"url": "$_id", "year": "$year", "tech": "$tech"}}}},
        {"$match": {"total": 2}},
        {"$sample": {"size": 100}}
    ])
    for obj in urls:
        techs = obj['techs']
        year = techs[0]['year']
        if 'web.archive.org' in techs[0]['url']:
            wayback_tech = techs[0]['tech']
            realweb_tech = techs[1]['tech']
        else:
            wayback_tech = techs[1]['tech']
            realweb_tech = techs[0]['tech']
        add_a, add_b = dict_diff(wayback_tech, realweb_tech)
        intersection = intersect_dict(wayback_tech, realweb_tech)
        sample.append({
            "url": obj['_id'],
            "year": year,
            "wayback delta": add_a,
            "realweb delta": add_b,
            "intersect": intersection
        })
    json.dump(sample, open('../tmp/tech_delta.json', 'w+'))


crawl_analyze_reorg()