"""
Stawman approach for Searching broken pages' content
"""
import requests
from urllib.parse import urlparse 
from pymongo import MongoClient
import pymongo
import re, os
from . import tools
from collections import defaultdict

import sys
sys.path.append('../')
from fable import config, tracer
from fable.tracer import tracer as tracing
from fable.utils import search, crawl, text_utils, url_utils

import logging
logging.setLoggerClass(tracer.tracer)
tracer = logging.getLogger('logger')
logging.setLoggerClass(logging.Logger)

db = config.DB
he = url_utils.HostExtractor()

def first_paragraph(text):
    lines = text.split('\n')
    has_words = [('', 0)]
    for line in lines:
        num_words = len(text_utils.tokenize(line))
        if num_words > 0:
            has_words.append((line, num_words))
            if len(has_words) > 3:
                return max(has_words, key=lambda x: x[1])[0]
    return max(has_words, key=lambda x: x[1])[0]

class StrawmanSearcher:
    def __init__(self, use_db=True, proxies={}, memo=None, similar=None):
        """
        At lease one of db or corpus should be provided
        # TODO: Corpus could not be necessary

        Return: 
            If found: URL, Trace (how copy is found, etc)
            else: None
        """
        self.PS = crawl.ProxySelector(proxies)
        self.use_db = use_db
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar() 

    def search(self, url, search_engine='bing'):
        global he
        if search_engine not in ['google', 'bing']:
            raise Exception("Search engine could support for google and bing")
        site = he.extract(url)
        if '://' not in site: site = f'http://{site}'
        _, final_url = self.memo.crawl(site, final_url=True)
        if final_url is not None:
            site = he.extract(final_url)
        try:
            wayback_url = self.memo.wayback_index(url)
            html = self.memo.crawl(wayback_url)
            title = self.memo.extract_title(html, version='domdistiller')
            content = self.memo.extract_content(html)
            tracer.wayback_url(url, wayback_url)
        except Exception as e:
            tracer.error(f'Exceptions happen when loading wayback verison of url: {str(e)}') 
            return
        tracer.title(url, title)
        search_results, searched = [], set()

        def search_once(search_results, typee):
            """Incremental Search"""
            global he
            nonlocal url, title, content, html, searched
            searched_contents = {}
            searched_titles = {}
            search_cand = [s for s in search_results if s not in searched]
            tracer.search_results(url, search_engine, typee, search_results)
            searched.update(search_results)
            for searched_url in search_cand:
                searched_html = self.memo.crawl(searched_url)
                if searched_html is None: continue
                searched_contents[searched_url] = self.memo.extract_content(searched_html)
                if he.extract(url) == he.extract(searched_url) or site == he.extract(searched_url):
                    searched_titles[searched_url] = self.memo.extract_title(searched_html)
            # TODO: May move all comparison techniques to similar class
            similars, fromm = self.similar.similar(url, title, content, searched_titles, searched_contents)
            if len(similars) > 0:
                top_similar = similars[0]
                return top_similar[0], {'type': fromm, 'value': top_similar[1]}
            return

        if title != '' and site:
            if search_engine == 'bing':
                site_str = f'site:{site}'
                search_results = search.bing_search(f'{title} {site_str}', use_db=self.use_db)
                if len(search_results) > 10: search_results = search_results[:10]
                similar = search_once(search_results, typee='title_site')
                if similar is not None: 
                    return similar
            else:
                search_results = search.google_search(f'{title}', site_spec_url=site, use_db=self.use_db)
                similar = search_once(search_results, typee='title_site')
                if similar is not None: 
                    return similar
        
        self.similar.tfidf._clear_workingset()
        fst_para = first_paragraph(content)
        tracer.topN(url, fst_para)
        if fst_para:
            if search_engine == 'bing':
                if site is not None:
                    site_str = f'site:{site}'
                else: 
                    site_str = ''
                fpara_q = fst_para[:1400 - len(site_str)] if len(fst_para) > 1400 - len(site_str) else fst_para
                search_results = search.bing_search(f'{fpara_q} {site_str}', use_db=self.use_db)
                if len(search_results) > 10: search_results = search_results[:10]
                similar = search_once(search_results, typee='first_para')
                if similar is not None: 
                    return similar
            else:
                fst_para = text_utils.tokenize(fst_para)
                if len(fst_para) > 32:
                    fst_para = fst_para[:32]
                fpara_q = ' '.join(fst_para)
                search_results = search.google_search(fpara_q, site_spec_url=site, use_db=self.use_db)
                similar = search_once(search_results, typee='first_para')
                if similar is not None:
                    return similar
        return


class StrawmanFinder:
    def __init__(self, use_db=True, db=db, memo=None, similar=None, proxies={}, tracer=None,\
                classname='fable_strawman', logname=None, loglevel=logging.INFO):
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar(short_threshold=0.75)
        self.PS = crawl.ProxySelector(proxies)
        self.searcher = StrawmanSearcher(memo=self.memo, similar=self.similar, proxies=proxies)
        self.db = db
        self.site = None
        self.classname = classname
        self.logname = classname if logname is None else logname
        self.tracer = tracer if tracer is not None else self._init_tracer(loglevel=loglevel)

    def _init_tracer(self, loglevel):
        logging.setLoggerClass(tracing)
        tracer = logging.getLogger('logger')
        logging.setLoggerClass(logging.Logger)
        tracer._set_meta(self.classname, logname=self.logname, db=self.db, loglevel=loglevel)
        return tracer
    
    def init_site(self, site, urls):
        self.site = site
        if len(self.tracer.handlers) > 2:
            self.tracer.handlers.pop()
        formatter = logging.Formatter('%(levelname)s %(asctime)s %(message)s')
        if not os.path.exists('logs'):
            os.mkdir('logs')
        file_handler = logging.FileHandler(f'./logs/{site}.log')
        file_handler.setFormatter(formatter)
        self.tracer.addHandler(file_handler)

    def clear_site(self):
        self.site = None
        self.logger.handlers.pop()

    def search(self, required_urls, title=True):
        if not title:
            self.similar.clear_titles()
        elif self.similar.site is None or self.site not in self.similar.site:
            self.similar.clear_titles()
            if not self.similar._init_titles(self.site):
                self.tracer.warn(f"Similar._init_titles: Fail to get homepage of {self.site}")
                return
        # !_search
        reorg_checked = list(self.db.reorg.find({"hostname": self.site, self.classname: {"$exists": True}}))
        reorg_checked = set([u['url'] for u in reorg_checked])
        broken_urls = set([ru for ru in required_urls if ru not in reorg_checked])

        self.tracer.info(f'Search SITE: {self.site} #URLS: {len(broken_urls)}')
        i = 0
        while len(broken_urls) > 0:
            url = broken_urls.pop()
            i += 1
            self.tracer.info(f'URL: {i} {url}')
            searched = self.searcher.search(url, search_engine='bing')
            if searched is None:
                searched = self.searcher.search(url, search_engine='google')
            update_dict = {}
            has_title = self.db.reorg.find_one({'url': url})
            # if has_title is None: # No longer in reorg (already deleted)
            #     continue
            if 'title' not in has_title:
                try:
                    wayback_url = self.memo.wayback_index(url)
                    html = self.memo.crawl(wayback_url)
                    title = self.memo.extract_title(html, version='domdistiller')
                except: # No snapthost on wayback
                    self.tracer.error(f'WB_Error {url}: Fail to get data from wayback')
                    try:
                        self.db.na_urls.update_one({'_id': url}, {"$set": {
                            'url': url,
                            'hostname': self.site,
                            'no_snapshot': True
                        }}, upsert=True)
                    except: pass
                    title = 'N/A'
            else:
                title = has_title['title']

            self.tracer.flush()

            if searched is not None:
                searched, trace = searched
                self.tracer.info(f"HIT: {searched}")
                update_dict.update({'reorg_url': searched, 'by':{
                    "method": "search"
                }})
                update_dict['by'].update(trace)

            try:
                self.db.reorg.update_one({'url': url}, {"$set": {self.classname: update_dict, "title": title}} ) 
            except Exception as e:
                self.tracer.warn(f'Search update DB: {str(e)}')