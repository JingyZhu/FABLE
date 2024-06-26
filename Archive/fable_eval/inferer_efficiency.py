from xmlrpc.client import ServerProxy
import pandas as pd
import pickle
from urllib.parse import urlsplit
import tools
from collections import defaultdict
import string
import time

import sys
sys.path.append('../')
from fable import config
from fable.utils import search, crawl, text_utils, url_utils, sic_transit

import logging
logger = logging.getLogger('logger')

def gen_path_pattern(url, dis=1):
    """
    Generate path patterns where all paths with same edit distance should follow
    # TODO: Currently only support edit distance of 1,  Could have larger dis
    """
    us = urlsplit(url)
    us = us._replace(netloc=us.netloc.split(':')[0])
    if us.path == '':
        us = us._replace(path='/')
    if us.path[-1] == '/' and us.path != '/':
        us = us._replace(path=us.path[:-1])
    path_lists = list(filter(lambda x: x!= '', us.path.split('/')))
    if us.query: 
        path_lists.append(us.query)
    patterns = []
    patterns.append(tuple(['*'] + path_lists))
    for i in range(len(path_lists)):
        path_copy = path_lists.copy()
        path_copy[i] = '*'
        patterns.append(tuple([us.netloc] + path_copy))
    return patterns


def pattern_match(pattern, url, dis=1):
    us = urlsplit(url)
    us = us._replace(netloc=us.netloc.split(':')[0])
    if us.path == '':
        us = us._replace(path='/')
    if us.path[-1] == '/' and us.path != '/':
        us = us._replace(path=us.path[:-1])
    path_lists = list(filter(lambda x: x!= '', us.path.split('/')))
    path_lists = [us.netloc] + path_lists
    if us.query: 
        path_lists.append(us.query)
    if len(pattern) != len(path_lists):
        return False
    for pat, path in zip(pattern, path_lists):
        if pat == '*': continue
        elif pat != path: return False
    return True

class Inferer:
    def __init__(self, proxies={}, memo=None, similar=None):
        self.PS = crawl.ProxySelector(proxies)
        self.proxy = ServerProxy(config.RPC_ADDRESS, allow_none=True)
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar()

    def infer(self, examples, urls, site='NA'):
        """
        Infer reorg urls of urls by learning the transformation rule in urls
        examples: list of ((urls, (other metadata)), reorg_url)
        urls: list of (urls, other metadata)
        Two metadata should be in the same format

        Returns: {url: [possible reorg_url]}
        # TODO: Create more sheets with similar/same #words
        """ 
        def normal(s):
            li = string.digits + string.ascii_letters + ' _-'
            rs = ''
            for ch in s:
                if ch in li: rs += ch
                else: rs += ' '
            return rs
        max_url, max_reorg_url = 0, 0
        output_query = False
        # Aligh URL to same length
        for ex_input, reorg_url in examples:
            url, _ = ex_input
            us = urlsplit(url)
            path_len = len(list(filter(lambda x: x != '', us.path.split('/')))) + 1
            if us.query: 
                output_query = True
                path_len += 1
            max_url = max(path_len, max_url)
            us_reorg = urlsplit(reorg_url)
            path_len = len(list(filter(lambda x: x != '', us_reorg.path.split('/')))) + 1
            if us_reorg.query:
                output_query=True
                path_len += 1
            max_reorg_url = max(path_len, max_reorg_url)

        for url, _ in urls:
            us = urlsplit(url)
            path_len = len(list(filter(lambda x: x != '', us.path.split('/')))) + 1
            if us.query: path_len += 1
            max_url = max(path_len, max_url)

        sheets = []
        sheet1_csv = defaultdict(list) # Both url and meta
        sheet2_csv = defaultdict(list) # Only meta
        sheet3_csv = defaultdict(list) # Only URL

        # Input the known input-output pair
        for ex_input, reorg_url in examples:
            url, meta = ex_input
            us = urlsplit(url)
            path_list = list(filter(lambda x: x != '', us.path.split('/')))
            url_inputs = [us.netloc.split(':')[0]] + path_list
            sheet2_csv['Site'].append(us.netloc.split(':')[0])
            if us.query: url_inputs.append(us.query)
            for i, url_piece in enumerate(url_inputs):
                sheet1_csv[f'URL{i}'].append(url_piece)
                sheet3_csv[f'URL{i}'].append(url_piece)
            if len(url_inputs) < max_url:
                for i in range(len(url_inputs), max_url):
                    sheet1_csv[f'URL{i}'].append('')
                    sheet3_csv[f'URL{i}'].append('')
            if isinstance(meta, tuple):
                for i, meta_piece in enumerate(meta):
                    sheet1_csv[f'Meta{2*i}'].append(normal(meta_piece))
                    sheet2_csv[f'Meta{2*i}'].append(normal(meta_piece))
                    sheet1_csv[f'Meta{2*i+1}'].append(normal(meta_piece.lower()))
                    sheet2_csv[f'Meta{2*i+1}'].append(normal(meta_piece.lower()))
            elif isinstance(meta, str):
                sheet1_csv[f'Meta0'].append(normal(meta))
                sheet2_csv[f'Meta0'].append(normal(meta))
                sheet1_csv[f'Meta1'].append(normal(meta.lower()))
                sheet2_csv[f'Meta1'].append(normal(meta.lower()))
            us_reorg = urlsplit(reorg_url)
            path_reorg_list = list(filter(lambda x: x != '', us_reorg.path.split('/')))
            url_reorg_inputs = [f"{us_reorg.scheme}://{us_reorg.netloc.split(':')[0]}"] + path_reorg_list
            if us_reorg.query: url_reorg_inputs.append(us_reorg.query)
            for i, reorg_url_piece in enumerate(url_reorg_inputs):
                sheet1_csv[f'Output_{i}'].append(reorg_url_piece)
                sheet2_csv[f'Output_{i}'].append(reorg_url_piece)
                sheet3_csv[f'Output_{i}'].append(reorg_url_piece)
            if i < max_reorg_url:
                for i in range(len(url_reorg_inputs), max_reorg_url):
                    sheet1_csv[f'Output_{i}'].append('')
                    sheet2_csv[f'Output_{i}'].append('')
                    sheet3_csv[f'Output_{i}'].append('')
        urls_idx = {}
        # Input the inferring examples
        for i, (url, meta) in enumerate(urls):
            us = urlsplit(url)
            urls_idx[url] = i + len(examples)
            # sheet1_csv['URL'].append(url)
            path_list = list(filter(lambda x: x != '', us.path.split('/')))
            url_inputs = [us.netloc.split(':')[0]] + path_list
            sheet2_csv['Site'].append(us.netloc.split(':')[0])
            if us.query: url_inputs.append(us.query)
            for i, url_piece in enumerate(url_inputs):
                sheet1_csv[f'URL{i}'].append(url_piece)
                sheet3_csv[f'URL{i}'].append(url_piece)
            if len(url_inputs) < max_url:
                for i in range(len(url_inputs), max_url):
                    sheet1_csv[f'URL{i}'].append('')
                    sheet3_csv[f'URL{i}'].append('')
            if isinstance(meta, tuple):
                for i, meta_piece in enumerate(meta):
                    sheet1_csv[f'Meta{2*i}'].append(normal(meta_piece))
                    sheet2_csv[f'Meta{2*i}'].append(normal(meta_piece))
                    sheet1_csv[f'Meta{2*i+1}'].append(normal(meta_piece.lower()))
                    sheet2_csv[f'Meta{2*i+1}'].append(normal(meta_piece.lower()))
            elif isinstance(meta, str):
                sheet1_csv[f'Meta0'].append(normal(meta))
                sheet2_csv[f'Meta0'].append(normal(meta))
                sheet1_csv[f'Meta1'].append(normal(meta.lower()))
                sheet2_csv[f'Meta1'].append(normal(meta.lower()))
            for i in range(max_reorg_url):
                sheet1_csv[f'Output_{i}'].append('')
                sheet2_csv[f'Output_{i}'].append('')
                sheet3_csv[f'Output_{i}'].append('')
        sheets = [sheet1_csv, sheet2_csv, sheet3_csv]
        sheets = [pickle.dumps({
            'sheet_name': f'sheet{i+1}',
            'csv': sheet
        }) for i, sheet in enumerate(sheets)]
        count = 0
        while count < 3:
            try:
                outputs = self.proxy.handle(sheets, site + str(time.time()))
                break
            except Exception as e:
                logger.error(f'infer: exception on RPC {str(e)}')
                count += 1
                time.sleep(2)
                continue
        outputs = [pickle.loads(o.data) for o in outputs]
        outputs = [pd.DataFrame(o['csv']) for o in outputs]
        poss_infer = defaultdict(set)
        for output in outputs:
            for url, _ in urls:
                idx = urls_idx[url]
                reorg_url_lists = output.filter(regex='^Output', axis=1).iloc[idx]
                num_outputs = len(reorg_url_lists)
                scheme_netloc = reorg_url_lists['Output_0']
                reorg_paths = []
                for j in range(1, num_outputs - output_query):
                    reorg_part = reorg_url_lists[f'Output_{j}']
                    if not isinstance(reorg_part, str) or reorg_part.lower() == 'nan':
                        continue
                    reorg_paths.append(reorg_part)
                # if len(reorg_paths) < num_outputs - output_query - 1:
                #     continue
                reorg_paths = '/'.join(reorg_paths)
                reorg_url = f'{scheme_netloc}/{reorg_paths}'
                if output_query:
                    reorg_url += f'?{reorg_url_lists[f"Output_{num_outputs-1}"]}'
                poss_infer[url].add(reorg_url)
        return {k: list(v) for k, v in poss_infer.items()}
    
    def if_reorg(self, url, reorg_urls, compare=True, fp_urls={}):
        """
        reorg_urls: all urls infered by inferer
        compare: whether to actually compare the content/title
        fp_urls: Used when compare=False. List of already known urls from output to avoid inference from infering on the same url
        return: Matched URLS, trace(dict)
        """
        reorg_content = {}
        reorg_title = {}
        if not compare:
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
                rval, _ = sic_transit.broken(reorg_url)
                if rval == False:
                    return reorg_url, {'type': "broken_check", "value": 'N/A'}
            if not new_reorg:
                return None, {'reason': 'No new reorg actually inferred'}
            else:
                return None, {'reason': 'Inferred urls broken'}
        else:
            for reorg_url in reorg_urls:
                reorg_html = self.memo.crawl(reorg_url)
                if reorg_html is None:
                    continue
                reorg_content[reorg_url] = self.memo.extract_content(reorg_html)
                reorg_title[reorg_url] = self.memo.extract_title(reorg_html)
            if len(reorg_content) + len(reorg_title) == 0:
                return None, {"reason": "reorg pages not exists"}
            try:
                wayback_url = self.memo.wayback_index(url)
                html = self.memo.crawl(wayback_url)
                if html is None: return None, {"reason": "url fail to load on wayback"}
                content = self.memo.extract_content(html)
                title = self.memo.extract_title(html)
            except:
                value = self.if_reorg(url, reorg_urls, compare=False, fp_urls=fp_urls)
                return value
                # return None, {"reason": "Fail to get wayback url, html or content/title"}
            similars, fromm = self.similar.similar(url, title, content, reorg_title, reorg_content)
            if len(similars) > 0:
                top_similar = similars[0]
                return top_similar[0], {'type': fromm, 'value': top_similar[1]}
            else:
                return None, {'reason': "no similar pages"}

    def if_reorg_check(self, url, reorg_urls, ground_truth_url):
        """
        Dummy function for determine if_reorg
        reorg_urls: all urls infered by inferer
        ground_truth_url: real alias url

        return: Matched URL, trace(dict)
        """
        reorg_url, trace = self.if_reorg(url, reorg_urls)

        if reorg_url is None:
            return 'N/A', {'reason': 'No urls determined by if_reorg'}
        reorg_content = {}
        reorg_title = {}

        if url_utils.url_match(reorg_url, ground_truth_url):
            return reorg_url, {'type': 'Same url', 'value': 'N/A'}
        reorg_html = self.memo.crawl(reorg_url)
        if reorg_html is None:
            return None, {'reason': 'Cannot get reorg_url html'}
        reorg_content[reorg_url] = self.memo.extract_content(reorg_html)
        reorg_title[reorg_url] = self.memo.extract_title(reorg_html)
        if len(reorg_content) + len(reorg_title) == 0:
            return None, {"reason": "reorg pages not exists"}
        try:
            html = self.memo.crawl(ground_truth_url)
            if html is None: return None, {"reason": "url fail to load"}
            content = self.memo.extract_content(html)
            title = self.memo.extract_title(html)
        except:
            return None, {"reason": "Fail to get ground truth url, html or content/title"}
        similars, fromm = self.similar.similar(url, title, content, reorg_title, reorg_content)
        if len(similars) > 0:
            top_similar = similars[0]
            return top_similar[0], {'type': fromm, 'value': top_similar[1]}
        else:
            return None, {'reason': "no similar pages"}