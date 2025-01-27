"""
Prepare sheet for study infer back.
Given the original broken URL
1). Looking for other broken URLs in the same format
2). Pick broken and close ones (with hist redir)
3). Format into a csv for testing
"""
import os
from re import L
from urllib.parse import urlsplit, parse_qs
import random
from dateutil import parser as dparser
import threading
from statistics import median

from . import config, tools, searcher, histredirector, inferer, tracer
from fable.utils import url_utils, crawl, sic_transit

he = url_utils.HostExtractor()

class NeighborAlias:
    def __init__(self, similar=None):
        self.memo = tools.Memoizer()
        self.similar = tools.Similar() is similar is None else similar
        self.histredirector = histredirector.HistRedirector()
        self.searcher = searcher.Searcher(memo=self.memo, similar=self.similar)
        
    def __detect_str_alnum(self, string):
        """Detect whether string has alpha and/or numeric char"""
        typee = ''
        alpha_char = [c for c in string if c.isalpha()]
        num_char = [c for c in string if c.isdigit()]
        if len(alpha_char) > 0:
            typee += 'A'
        if len(num_char) > 0:
            typee += 'N'
        return typee

    def _get_filename_alnum(self, url):
        path = urlsplit(url).path
        filename = list(filter(lambda x: x!='', path.split('/')))
        if len(filename) == 0:
            filename = ''
        else:
            filename = filename[-1]
        filename, _ = os.path.splitext(filename)
        return self.__detect_str_alnum(filename)

    def _length(self, url):
        path = urlsplit(url).path
        path = list(filter(lambda x: x!='', path.split('/')))
        return len(path)

    def _same_pattern(self, url1, url2):
        # * Filter out same urls
        if url_utils.url_match(url1, url2):
            return False
        # return url_utils.netloc_dir(url1)==url_utils.netloc_dir(url2) \
        #         and _length(url1)==_length(url2)\
        #         and _get_filename_alnum(url1)==_get_filename_alnum(url2)
        return self._length(url1)== self._length(url2)
                # and self._get_filename_alnum(url1)== self._get_filename_alnum(url2)

    def _order_neighbors(self, target_urls, neighbors, ts):
        """Order the neighbors so that most similar neighbor (in location/format and in time) can be tested first"""
        all_neighbors = []
        target_urls = random.sample(target_urls, min(5, len(target_urls)))
        print("Sampled target urls:", target_urls)
        for target_url in target_urls:
            all_neighbors += url_utils.order_neighbors(target_url, neighbors, urlgetter=lambda x: x[1], ts=ts)
        all_neighbors.sort(key=lambda x: x[2][0], reverse=True)
        # * dedup
        uniq_neighbor_score, seen = [], set()
        for neighbor in all_neighbors:
            keyneighbor = url_utils.url_norm(neighbor[1], wayback=True, case=True, trim_www=True,\
                trim_slash=True, ignore_scheme=True)
            if keyneighbor in seen:
                continue
            seen.add(keyneighbor)
            uniq_neighbor_score.append(neighbor)
        return uniq_neighbor_score

    def _find_alias(self, url, speed=1, spec_method=[]):
        """
        speed (if no spec_method): level of speed. 
        0: Only hist redir, 
        1: Hist redir + search

        spec_method: specified method. speed will be abandoned
                        use: wayback_alias, search, backlink_basic, backlink
        """
        site = he.extract(url)
        self.similar._init_titles(site) 
        results = {'hist_redir': (None, {}), 'hist_redir_any': (None, {}), 'search': (None, {})}
        # * Decide what to run based on speed or spec_method
        run_dict = {
            "hist_redir": False,
            'search': False, 
            "search_fuzzy": False
        }
        if len(spec_method) > 0:
            for sm in spec_method: run_dict[sm] = True
        else:
            run_dict['hist_redir'] = True
            if speed > 0: 
                run_dict['search'] = True

        def _wayback_alias(url):
            if not run_dict['hist_redir']:
                return
            alias = self.histredirector.wayback_alias_history(url)
            results['hist_redir'] = alias, {'method': 'wayback_alias'}
        def _search(url):
            if run_dict['search']:
                alias = self.searcher.search(url, search_engine='bing')
                if alias[0] is None:
                    alias = self.searcher.search(url, search_engine='google')
                alias[1].update({'method': 'search'})
                results['search'] = alias
            if run_dict['search_fuzzy']:
                alias = self.searcher.search(url, search_engine='bing', fuzzy=True)
                if alias[0] is None:
                    alias = self.searcher.search(url, search_engine='google', fuzzy=True)
                search_aliases = []
                seen = set()
                if alias[0]:
                    for f in alias:
                        reason = {'method': 'search'}
                        reason.update(f[1])
                        seen.add(f[0])
                        search_aliases.append([f[0], reason])
                all_search = self.searcher.search_results(url)
                for ase in all_search:
                    if ase in seen: continue
                    seen.add(ase)
                    search_aliases.append([ase, {'method': 'search', 'type': 'fuzzy_search'}])
                if len(search_aliases) > 0:
                    results['search'] = search_aliases
        threads = []
        threads.append(threading.Thread(target=_wayback_alias, args=(url,)))
        threads.append(threading.Thread(target=_search, args=(url,)))
        for i in range(len(threads)):
            threads[i].start()
        for i in range(len(threads)):
            threads[i].join()
        return results

    def _non_broken_alias(self, url):
        """Assume the url is not broken"""
        html, final_url = self.memo.crawl(url, final_url=True)
        if final_url and not url_utils.url_match(url, final_url):
            return crawl.get_canonical(final_url, html)
        return

    def get_neighbors(self, urls, tss=[], status_filter='23'):
        """Get neighbors (in order)"""
        url = urls[0]
        netdir = url_utils.netloc_dir(url, exclude_index=True)
        url_dir = netdir[1]
        count = 0
        neighbors = []
        seen_neighbors = set()
        while count < 3 and url_dir != "/" and len(seen_neighbors) < 10:
            q = netdir[0] + url_dir + '/*'
            param_dict = {
                'url': q,
                'filter': ['mimetype:text/html', f'statuscode:[{status_filter}][0-9]*'],
                # 'collapse': ['urlkey'],
                'output': 'json',
            }
            w, _ = crawl.wayback_index(q, param_dict=param_dict)
            print(f"First query {q}: {len(w)}")
            same_w = [ww for ww in w if self._same_pattern(url, ww[1])]
            print(f"Second pattern: {len(same_w)}")
            neighbors += same_w
            seen_neighbors = set([u[1] for u in neighbors])
            count += 1
            url_dir = url_utils.nondigit_dirname(url_dir)
        
        tss = [url_utils._safe_dparse(ts) for ts in tss if ts and isinstance(ts, str)]
        ts = median(tss) if len(tss) > 0 else None
        ordered_w = self._order_neighbors(urls, neighbors, ts)
        print('length ordered_w', len(ordered_w))
        return ordered_w

    def neighbor_aliases(self, urls, title=False, tss=[], speed=1, spec_method=[],
                        max_keep=None, status_filter='23', max_trials=10):                
        """
        Looking for other similar URLs' aliases
        urls: str/list. If list, randomly pick 5 (most) and look for their closed neighbors all together
        speed: how much do we want to look for the alias (only hist redir --> everything)
        order: how the aliases found by different technique should be ordered
        max_keep: Max aliases to keep per each URL
        """
        if isinstance(urls, str):
            urls = [urls]
        url = urls[0]
        if title:
            site = he.extract(url)
            self.similar._init_titles(site)
        ordered_w = self.get_neighbors(urls, tss=tss, status_filter=status_filter)
        ordered_w = ordered_w[:min(len(ordered_w), 20)]
        
        print("Ordered_w", ordered_w)
        sheet_dict = {'examples': [], 'urls': []}

        row = 0
        print('Total candidates:', len(ordered_w))
        sheet_dict['urls'].append((url, (title,)))
        total = 0 # * Total #URLs that have been tried to find an alias
        for _, orig_url, _ in ordered_w:
            if total >= max_trials: # * Test at most max_trials urls
                break
            print(total, orig_url)
            broken, reason = sic_transit.broken(orig_url, html=True)
            wayback_url = self.memo.wayback_index(orig_url)
            title = ''
            if wayback_url: 
                wayback_html = self.memo.crawl(wayback_url)
                title = self.memo.extract_title(wayback_html)
            if broken != True:
                print(f"URL not broken: {orig_url} {reason}")
                alias = self._non_broken_alias(orig_url)
                if alias and not url_utils.url_match(orig_url, alias):
                    print(f"redirect alias: {orig_url} --> {alias}")
                    trace = {"method": "redirection", "type": "redirection"}
                    sheet_dict['examples'].append((orig_url, (title,), alias, trace))
                    row += 1
                continue
            total += 1
            aliases = []
            alias_dict = self._find_alias(orig_url, speed=speed, spec_method=spec_method)
            for v in alias_dict.values(): 
                if v[0] is None: continue
                if isinstance(v[0], list): aliases += v
                else: aliases.append(v)
            if len(aliases) <= 0:
                print('no alias')
                continue
            if max_keep:
                aliases = aliases[:min(max_keep, len(aliases))]
            print(f'alias: {orig_url} --> {[a[0] for a in aliases]}')
            wayback_url = self.memo.wayback_index(orig_url)
            title = ''
            if wayback_url: 
                wayback_html = self.memo.crawl(wayback_url)
                title = self.memo.extract_title(wayback_html)
            for alias, trace in aliases:
                sheet_dict['examples'].append((orig_url, (title,), alias, trace))
                row += 1
            
        sheet_dict['trials'] = total
        return sheet_dict