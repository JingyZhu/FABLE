from xmlrpc.client import ServerProxy
import pandas as pd
import numpy as np
import pickle
from urllib.parse import urlsplit, parse_qsl, parse_qs
from collections import defaultdict
import string
import time
import socket
import os
import regex

from . import config, tools, tracer
from .utils import crawl, sic_transit, url_utils

import logging
logging.setLoggerClass(tracer.tracer)
tracer = logging.getLogger('logger')
logging.setLoggerClass(logging.Logger)

ISNUM = lambda x: type(x).__module__ == np.__name__ or isinstance(x, int)
VERTICAL_BAR_SET = '\u007C\u00A6\u2016\uFF5C\u2225\u01C0\u01C1\u2223\u2502\u0964\u0965'


def normal_hostname(hostname):
    hostname = hostname.split(':')[0]
    hostname = hostname.split('.')
    if hostname[0] == 'www': hostname = hostname[1:]
    return '.'.join(hostname)

def soft_404_content(reason):
    if not isinstance(reason, list):
        return False
    for r in reason:
        if r != "Similar soft 404 content":
            return False
    return True

class Inferer:
    def __init__(self, proxies={}, memo=None, similar=None):
        self.PS = crawl.ProxySelector(proxies)
        self.proxy = ServerProxy(config.RPC_ADDRESS, allow_none=True)
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar()
        self.not_workings = set() # Seen broken inferred URLs
        self.site = None
        self.url_aliases = defaultdict(set) # * Reorg pairs that have been added
        self.url_meta = {} # * {URL: Meta}
        self.upd = url_utils.URLPatternDict(max_diff=2) # * URLPatternDict for clustering input URLs

    def init_site(self, site):
        if self.site:
            self.clear_site()
        self.site = site

    def clear_site(self):
        self.site = None
        self.url_aliases = defaultdict(set)
        self.url_meta = {}
        self.upd = url_utils.URLPatternDict(max_diff=2)

    def add_url_alias(self, url, meta, reorg):
        """
        Only applies to same domain currently
        meta: [title]
        Return bool on whether success
        """
        # if he.extract(reorg) != he.extract(url):
        #     return False

        if url in self.url_aliases and reorg in self.url_aliases[url]:
            return False
        else:
            self.upd.add_url(url)
            self.url_aliases[url].add(reorg)
        
        if meta[0] == 'N/A':
            meta[0] = ''
        self.url_meta[url] = meta
        return True
    
    def add_url(self, url, meta):
        """
        Add URLs required to infer
        """
        if url in self.url_meta:
            return
        if meta[0] == 'N/A':
            meta[0] = ''
        self.upd.add_url(url)
        self.url_meta[url] = meta
    
    def add_urls(self, url_metas):
        for url, meta in url_metas:
            self.add_url(url, meta)

    def infer(self, examples, urls):
        """
        Infer reorg urls of urls by learning the transformation rule in urls
        examples: list of ((urls, (other metadata)), reorg_url)
        urls: list of (urls, other metadata)
        Two metadata should be in the same format

        Returns: {url: [possible reorg_url]}
        # TODO: Create more sheets with similar/same #words
        """ 
        def normal(s):
            tokens = regex.split(f'_| [{VERTICAL_BAR_SET}] |[{VERTICAL_BAR_SET}]| \p{{Pd}} ', s)
            if len(tokens) > 1:
                s = tokens[0]
            li = string.digits + string.ascii_letters + ' _-'
            rs = ''
            for ch in s:
                if ch in li: rs += ch
                elif ch == "'": continue
                else: rs += ' '
            return rs
        
        def insert_url(sheet, row, url):
            """Insert the original (broken) URL part into the sheet"""
            us = urlsplit(url)
            path_list = list(filter(lambda x: x != '', us.path.split('/')))
            url_inputs = [normal_hostname(us.netloc)] + path_list
            for j, url_piece in enumerate(url_inputs):
                sheet.loc[row, f'URL{j}'] = url_piece
                qs = url_utils.my_parse_qs(us.query)
            for key, value in qs.items():
                if key == 'NoKey':
                    sheet.loc[row, f'Query_{key}'] = value[0]
                else:
                    sheet.loc[i, f'Query_{key}'] = f'{key}={value[0]}'
            return sheet

        def insert_metadata(sheet, row, meta, expand=True):
            """Expand: Whether to expand the metadata into different form"""
            for j, meta_piece in enumerate(meta):
                if expand:
                    sheet.loc[row, f'Meta{j}'] = normal(meta_piece)
                    sheet.loc[row, f'Meta{j+0.5}'] = normal(meta_piece.lower())
                else:
                    sheet.loc[row, f'Meta{j}'] = meta_piece
            return sheet
        
        def insert_reorg(sheet, row, reorg):
            """Insert alias part into the sheet"""
            us_reorg = urlsplit(reorg)
            path_reorg_list = list(filter(lambda x: x != '', us_reorg.path.split('/')))
            url_reorg_inputs = [f"https://{normal_hostname(us_reorg.netloc)}"] + path_reorg_list
            for j, reorg_url_piece in enumerate(url_reorg_inputs):
                sheet.loc[row, f'Output_{j}'] = reorg_url_piece
                qs_reorg = url_utils.my_parse_qs(us_reorg.query)
            for key, value in qs_reorg.items():
                if key == 'NoKey':
                    sheet.loc[i, f'Output_Q_{key}'] = value[0]
                else:
                    sheet.loc[i, f'Output_Q_{key}'] = f'{key}={value[0]}'
            return sheet
                
        sheet1 = pd.DataFrame() # Both url and meta
        sheet2 = pd.DataFrame() # Only meta
        sheet3 = pd.DataFrame() # Only URL
        # * Input examples
        for i, (url, meta, reorg_url) in enumerate(examples):
            # * Input URL part
            sheet1 = insert_url(sheet1, i, url)
            sheet3 = insert_url(sheet3, i, url)
            # * Input Metadata part
            sheet1 = insert_metadata(sheet1, i, meta, expand=True)
            sheet2 = insert_metadata(sheet2, i, meta, expand=True)
            # * Input Reorg Part
            sheet1 = insert_reorg(sheet1, i, reorg_url)
            sheet2 = insert_reorg(sheet2, i, reorg_url)
            sheet3 = insert_reorg(sheet3, i, reorg_url)

        url_idx = {}
        # * Input the to infer examples
        for i, (url, meta) in enumerate(urls):
            counter = i+len(examples)
            url_idx[url] = counter
            # * Input URL part
            sheet1 = insert_url(sheet1, counter, url)
            sheet3 = insert_url(sheet3, counter, url)
            # * Input Metadata part
            sheet1 = insert_metadata(sheet1, counter, meta, expand=True)
            sheet2 = insert_metadata(sheet2, counter, meta, expand=True)
        
        # * RPC formatted dataframe to FlashFill
        sheets = [sheet1, sheet2, sheet3]
        sheets = [pickle.dumps({
            'sheet_name': f'sheet{i+1}',
            'csv': sheet
        }) for i, sheet in enumerate(sheets)]
        count = 0
        while count < 3:
            try:
                # socket.setdefaulttimeout(20)
                outputs = self.proxy.handle(sheets, self.site + str(time.time()))
                # socket.setdefaulttimeout(None)
                break
            except Exception as e:
                tracer.error(f'infer: exception on RPC {str(e)}')
                count += 1
                time.sleep(2)
                continue
        if count == 3:
            return {}
        outputs = pickle.loads(outputs.data)
        outputs = [o['csv'] for o in outputs]
        poss_infer = defaultdict(set) # * Any results inferred from 3 sheets
        seen_reorg = set()
        for output in outputs:
            for url, meta in urls:
                idx = url_idx[url]
                reorg_url_lists = output.filter(regex='^Output_\d', axis=1).iloc[idx]
                reorg_query_lists = output.filter(regex='^Output_Q', axis=1).iloc[idx]
                num_url_outputs = len(reorg_url_lists)
                scheme_netloc = reorg_url_lists['Output_0']
                reorg_paths = []
                for j in range(1, num_url_outputs):
                    reorg_part = reorg_url_lists[f'Output_{j}']
                    # TODO: How to deal with nan requires more thoughts
                    if reorg_part != reorg_part: # * Check for NaN value (trick)
                        continue
                    if ISNUM(reorg_part): reorg_part = str(int(reorg_part))
                    reorg_paths.append(reorg_part)
                reorg_paths = '/'.join(reorg_paths)
                reorg_url = f'{scheme_netloc}/{reorg_paths}'
                reorg_queries = []
                for key in reorg_query_lists:
                    reorg_kv = reorg_query_lists[f'Output_Q_{key}']
                    if reorg_kv != reorg_kv or (key != "NoKey" and not reorg_kv.split('=')[1]):
                        continue
                    if ISNUM(reorg_kv): reorg_kv = str(int(reorg_kv))
                    reorg_queries.append(reorg_kv)
                if len(reorg_queries) > 0:
                    reorg_url += f"?{'&'.join(reorg_queries)}"
                
                if reorg_url not in seen_reorg:
                    tracer.inference(url, meta, examples, reorg_url)
                    seen_reorg.add(reorg_url)
                poss_infer[url].add(reorg_url)
        return {k: list(v) for k, v in poss_infer.items()}
    
    def _construct_input_output(self, match):
        """
        Given a pattern of URLs, output a sheet for RPC inference

        Return: examples: [(url, meta, reorg)], to_infer: [(url, meta)]
        """
        tracer.debug(f"_construct_input_output: {match['pattern']}")
        matched_urls = match['urls']
        output_upd = url_utils.URLPatternDict(max_diff=2)
        examples, toinfer = [], []
        alias_url = defaultdict(list) # * Reverse index
        # * Filter out multiple inputs having same output URL
        for matched_url in matched_urls: 
            if len(self.url_aliases.get(matched_url, set())) > 0: # * Has alias
                for matched_alias in self.url_aliases[matched_url]:
                    alias_url[matched_alias].append(matched_url)
        for matched_url in matched_urls:
            if len(self.url_aliases.get(matched_url, set())) > 0: # * Has alias
                for matched_alias in self.url_aliases[matched_url]:
                    if len(alias_url[matched_alias]) > 1:
                        continue
                    output_upd.add_url(matched_alias)
            else: # * To infer
                cell = (matched_url, self.url_meta[matched_url])
                toinfer.append(cell)
        # * Construct examples (intput)
        good_outputs = output_upd.pop_matches()
        good_outputs.sort(key=lambda x: len(x['urls']), reverse=True)
        # * Pick most common output pattern, and construct sheet
        for good_output in good_outputs[0]['urls']:
            input_url = alias_url[good_output][0]
            cell = (input_url, self.url_meta[input_url], good_output)
            examples.append(cell)
        return examples, toinfer
    
    def infer_new(self, example):
        """
        When given a new example, infer all to-find related
        Return: {url: (found_alias, reason)}
        """
        url, meta, alias = example
        self.url_aliases[url].add(alias)
        self.url_meta[url] = meta
        self.upd.add_url(url)
        matched_urls = self.upd.match_url(url)
        found_alias = {}
        for match in matched_urls:
            examples, toinfer = self._construct_input_output(match)
            print(examples, toinfer)
            if len(examples) == 0:
                tracer.debug(f'infer_new: No (enough) inputs can be constructed from this pattern')
                continue
            possible_infer = self.infer(examples, toinfer)
            for infer_url, cands in possible_infer.items():
                alias, reason = self._verify_alias(infer_url, cands)
                if alias:
                    tracer.info(f"Found by infer: {infer_url} --> {alias} reason: {reason['type']}")
                    found_alias[infer_url] = alias
        return found_alias
    
    def infer_all(self):
        """
        Infer on all patterns added to inferer
        
        Return: {url: (found_alias, reason)}
        """
        found_alias = {}
        for match in self.upd.pop_matches(least_match=3):
            examples, toinfer = self._construct_input_output(match)
            if len(examples) == 0:
                tracer.debug(f'infer_new: No (enough) inputs can be constructed from this pattern')
                continue
            possible_infer = self.infer(examples, toinfer)
            for infer_url, cands in possible_infer.items():
                alias, reason = self._verify_alias(infer_url, cands)
                if alias:
                    tracer.info(f"Found by infer: {infer_url} --> {alias} reason: {reason['type']}")
                    found_alias[infer_url] = alias
        return found_alias
       

    def _verify_alias(self, url, reorg_urls, compare=True):
        """
        reorg_urls: all urls infered by inferer
        compare: whether to actually compare the content/title
        return: Matched URLS, trace(dict)
        """
        reorg_content = {}
        reorg_title = {}
        working_aliases = set()
        # * 1. Check breakage of inferred candidates
        new_reorg = False
        for reorg_url in reorg_urls:
            # Try:
            if urlsplit(url).path not in ['', '/'] and urlsplit(reorg_url).path in ['', '/']:
                continue
            # End of Try
            # match = [url_utils.url_match(reorg_url, fp_url) for fp_url in fp_urls]
            # if True in match:
            #     continue
            new_reorg = True
            if reorg_url in self.not_workings:
                tracer.debug('Inferred URL already checked broken')
            reorg_broken, reason = sic_transit.broken(reorg_url, html=True)
            if reorg_broken == True and not soft_404_content(reason): # * Broken
                self.not_workings.add(reorg_url)
            else:
                working_aliases.add(reorg_url)

        def return_noncompare():
            """No more information available than whether URLs are working or not"""
            nonlocal working_aliases, new_reorg
            if len(working_aliases) >= 0:
                # TODO: What if len(working_aliases) > 1?
                return list(working_aliases)[0], {'type': "nocomp_check", "value": 'N/A'}
            elif not new_reorg:
                return None, {'reason': 'No new reorg actually inferred'}
            else:
                return None, {'reason': 'Inferred urls broken'}

        if not compare:
            return return_noncompare()
        # * Compare version
        # * 2. Get URL's title & Content
        for reorg_url in reorg_urls: # * Check whether reorg_url is broken
            if reorg_url in self.not_workings:
                tracer.debug('Inferred URL already checked broken')
                continue
            reorg_html = self.memo.crawl(reorg_url)
            if reorg_html is None:
                continue
            reorg_content[reorg_url] = self.memo.extract_content(reorg_html)
            reorg_title[reorg_url] = self.memo.extract_title(reorg_html)
        if len(reorg_content) + len(reorg_title) == 0: # * No available content or title
            return return_noncompare()
        wayback_available = False
        try:
            wayback_url = self.memo.wayback_index(url)
            html = self.memo.crawl(wayback_url)
            if html is None: return None, {"reason": "url fail to load on wayback"}
            content = self.memo.extract_content(html)
            title = self.memo.extract_title(html)
            wayback_available = True
        except:
            pass
        # * 3.1 Match title/content
        if wayback_available:
            similars, fromm = self.similar.similar(wayback_url, title, content, reorg_title, reorg_content)
            if len(similars) > 0:
                top_similar = similars[0]
                return top_similar[0], {'type': fromm, 'value': top_similar[1]}
            else:
                return return_noncompare()
        # * 3.2 Match tokens
        else: # * Compare token instead
            alias_tokens = {}
            available_tokens = tools.get_unique_token(url)
            for alias in working_aliases:
                alias_tokens[alias] = tools.tokenize_url(alias)
            token_simi = self.similar.token_similar(url, available_tokens, alias_tokens)[:2]
            if self.similar._separable(token_simi):
                top_similar = token_simi[0]
                return top_similar[0], {'type': "token", 'value': top_similar[-1], 'matched_token': top_similar[1]}
            else:
                return return_noncompare()