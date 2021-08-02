"""
Search broken pages' content
"""
import re, regex, os
from urllib.parse import urlsplit, urlunsplit

from . import config, tools, tracer
from .utils import search, crawl, text_utils, url_utils

import logging
logging.setLoggerClass(tracer.tracer)
tracer = logging.getLogger('logger')
logging.setLoggerClass(logging.Logger)

he = url_utils.HostExtractor()
VERTICAL_BAR_SET = '\u007C\u00A6\u2016\uFF5C\u2225\u01C0\u01C1\u2223\u2502\u0964\u0965'

class Searcher:
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

    def _get_unique_token(self, url):
        """Given a URL, return which tokens should be put into search engine"""
        us = urlsplit(url)
        path = us.path
        if path == '': path = '/'
        if path[-1] == '/' and path != '/': path = path[:-1]
        path = path.split('/')
        available_tokens = []
        params = {
            'output': 'json',
            "limit": 10,
            'collapse': 'urlkey',
            'filter': ['statuscode:200', 'mimetype:text/html'],
        }
        for i in range(len(path)-1, 0, -1):
            sub_path = '/'.join(path[:i+1])
            sub_us = us._replace(path=sub_path + '*', query='', fragment='')
            sub_url = urlunsplit(sub_us)
            wayback_index, _ = crawl.wayback_index(sub_url, param_dict=params)
            # print(sub_url)
            tracer.debug(f'_get_unique_token: {sub_url}, {len(wayback_index)}')
            if len(wayback_index) <= 1:
                available_tokens.append(path[i])
            else:
                break
        return available_tokens
    
    def _tokenize_url(self, url):
        path = urlsplit(url).path
        if path == '': path = '/'
        if path[-1] == '/' and path != '/': path = path[:-1]
        path = path.split('/')
        tokens = []
        for p in path:
            token = os.path.splitext(p)[0]
            token = regex.split("[^a-zA-Z1-9]", token)
            token = ' '.join(token)
            tokens.append(token)
        return tokens

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
            html = self.memo.crawl(wayback_url, proxies=self.PS.select())
            title = self.memo.extract_title(html, version='domdistiller')
            content = self.memo.extract_content(html)
            tracer.wayback_url(url, wayback_url)
        except Exception as e:
            tracer.error(f'Exceptions happen when loading wayback verison of url: {str(e)}') 
            wayback_url = url_utils.constr_wayback(url, '20211231')
            return
        tracer.title(url, title)
        search_results, searched = [], set()

        def search_once(search_results, typee):
            """Incremental Search"""
            global he
            nonlocal url, title, content, html, searched, search_engine
            searched_contents = {}
            searched_titles = {}
            search_cand = [s for s in search_results if s not in searched]
            tracer.search_results(url, search_engine, typee, search_results)
            searched.update(search_results)
            for searched_url in search_cand:
                # * Use earliest archived copy if available
                searched_wayback = self.memo.wayback_index(searched_url, policy='earliest')
                searched_url_rep = searched_wayback if searched_wayback else searched_url
                searched_html = self.memo.crawl(searched_url_rep, proxies=self.PS.select())
                if searched_html is None: continue
                searched_contents[searched_url] = self.memo.extract_content(searched_html)
                if he.extract(url) == he.extract(searched_url) or site == he.extract(searched_url):
                    searched_titles[searched_url] = self.memo.extract_title(searched_html)
            similars, fromm = self.similar.similar(wayback_url, title, content, searched_titles, searched_contents)
            if len(similars) > 0:
                top_similar = similars[0]
                return top_similar[0], {'type': fromm, 'value': top_similar[1]}
            return

        # * Search with title
        if title != '' and site:
            if search_engine == 'bing':
                # * Bing Title
                site_str = f'site:{site}'
                bing_title = regex.split(f'_| [{VERTICAL_BAR_SET}] |[{VERTICAL_BAR_SET}]| \p{{Pd}} |\p{{Pd}}', title)
                bing_title = ' '.join(bing_title)
                search_results = search.bing_search(f'{bing_title} {site_str}', use_db=self.use_db)
                if len(search_results) > 20: search_results = search_results[:20]
                similar = search_once(search_results, typee='title_site')
                if similar is not None: 
                    return similar
                if len(search_results) >= 8:
                    search_results = search.bing_search(f'+"{bing_title}" {site_str}', use_db=self.use_db)
                    if len(search_results) > 20: search_results = search_results[:20]
                    similar = search_once(search_results, typee='title_exact')
                    if similar is not None: 
                        return similar
            else:
                # * Google Title
                search_results = search.google_search(f'{title}', site_spec_url=site, use_db=self.use_db)
                similar = search_once(search_results, typee='title_site')
                if similar is not None: 
                    return similar
                if len(search_results) >= 8:
                    search_results = search.google_search(f'"{title}"', site_spec_url=site, use_db=self.use_db)
                    similar = search_once(search_results, typee='title_exact')
                    if similar is not None: 
                        return similar
        
        # * Search with token
        available_tokens = self._get_unique_token(url)
        tracer.token(url, available_tokens)
        search_results = []
        for i, token in enumerate(available_tokens):
            token = os.path.splitext(token)[0]
            token = regex.split("[^a-zA-Z0-9]", token)
            token = ' '.join(token)
            if search_engine == 'bing':
                # * Bing
                search_results = search.bing_search(f'instreamset:url:{token} site:{site}', use_db=self.use_db)
                tracer.search_results(url, 'bing', f"token_{i}", search_results)
            else:
                # * Google
                search_results = search.google_search(f'inurl:{token}', site_spec_url=site, use_db=self.use_db)
                tracer.search_results(url, 'google', f"token_{i}", search_results)
            search_tokens = {}
            for sr in search_results:
                tokens = self._tokenize_url(sr)
                search_tokens[sr] = tokens
            token_simi = self.similar.token_similar(url, token, search_tokens)[:2]
            if self.similar._separable(token_simi):
                top_similar = token_simi[0]
                return top_similar[0], {'type': "token", 'value': top_similar[-1], 'matched_token': top_similar[1]}

        # * Search with content
        self.similar.tfidf._clear_workingset()
        topN = self.similar.tfidf.topN(content)
        topN = ' '.join(topN)
        tracer.topN(url, topN)
        search_results = []
        if len(topN) > 0:
            if search_engine == 'bing':
                # * Bing Content
                if site is not None:
                    site_str = f'site:{site}'
                else: 
                    site_str = ''
                search_results = search.bing_search(f'{topN} {site_str}', use_db=self.use_db)
                if len(search_results) > 20: search_results = search_results[:20]
            else:
                # * Google Content
                search_results = search.google_search(topN, site_spec_url=site, use_db=self.use_db)
            similar = search_once(search_results, typee='topN')
            if similar is not None: 
                return similar
        return