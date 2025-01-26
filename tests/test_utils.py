import pytest
import logging
import os

from fable import config
from fable.utils import url_utils, crawl, text_utils
import json
from concurrent import futures

he = url_utils.HostExtractor()
db = config.DB

def _diffs(url, alias):
    url_tokens = url_utils.tokenize_url(url, include_all=True)
    alias_tokens = url_utils.tokenize_url(alias, include_all=True)
    example_diffs = url_utils.url_token_diffs(url_tokens, alias_tokens)
    return tuple(sorted(e[:2] for e in example_diffs))

def test_url_token_diffs():
    same_diffs = [
        [
            ('http://pc.ign.com/articles/121/1212033p1.html', 'https://www.ign.com/articles/2011/11/10/the-elder-scrolls-v-skyrim-review'), 
            ('http://pc.ign.com/articles/808/808367p1.html', 'https://www.ign.com/articles/2007/07/26/gears-of-war-pc-qa'),
            ('http://pc.ign.com/articles/159/159942p1.html', 'https://www.ign.com/articles/1999/01/19/baldurs-gate-6')
        ]
    ]
    for examples in same_diffs:
        diffs = None
        for url, alias in examples:
            diff = _diffs(url, alias)
            if diffs is None: diffs = diff
            assert(diffs == diff)

def test_lang_detect():
    def get_lang(i, url):
        print(i, url)
        html = crawl.requests_crawl(url, raw=True)
        r = {'site': url, 'lang_meta': None, 'fuzzy': None, 'all': None}
        r['lang_meta'] = text_utils._lang_meta(html)
        r['fuzzy'] = text_utils._fuzzy_lang(html)
        r['all'] = text_utils.detect_lan(html, fuzzy=True)
        return r

    site_lan = json.load(open('test_data/site_lan_test.json', 'r'))
    inconsistent = []
    with futures.ThreadPoolExecutor(max_workers=10) as e:
        rs = []
        for i, site in enumerate(site_lan):
            rs.append(e.submit(get_lang, i, site))
        for r in rs:
            r = r.result()
            s = set([r['lang_meta'], r['fuzzy'], r['all']])
            if len(s) > 1:
                inconsistent.append(r)
    json.dump(inconsistent, open('test_data/site_lan_test_inconsistent.json', 'w+'), indent=2)

def test_domdistiller():
    url = 'https://web.archive.org/web/20140702013724/http://www.sup.org/book.cgi?id=22655'
    html = crawl.requests_crawl(url)
    title = text_utils.extract_title(html, version='domdistiller')
    content = text_utils.extract_body(html, version='domdistiller')
    print("Title:", title)
    print("Content:", content)

# test_lang_detect()
test_domdistiller()