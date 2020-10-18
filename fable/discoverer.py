"""
Discover backlinks to today's page
"""
import os
from urllib.parse import urlsplit, urlparse, parse_qsl, parse_qs, urlunsplit
from itertools import chain, combinations
from bs4 import BeautifulSoup
from queue import Queue
from collections import defaultdict
import re, json
import random
from dateutil import parser as dparser
import datetime

from . import config, tools
from .utils import search, crawl, text_utils, url_utils, sic_transit

import logging
logger = logging.getLogger('logger')

he = url_utils.HostExtractor()

BUDGET = 4
GUESS_DEPTH = 3
OUTGOING = 1
MIN_GUESS = 6
CUT = 30

def wsum_simi(simi):
    return 3/4*simi[0] + 1/4*simi[1]

def estimated_score(spatial, simi):
    """
    similarity consider as the possibilities of hitting in next move
    Calculated as kp*1 + (1-kp)*n 
    """
    p = 3/4*simi[0] + 1/4*simi[1]
    k = 1
    return k*p + (1-k*p)*spatial


def tree_diff(dest, src):
    seq1, seq2 = [], []
    us1, us2 = urlsplit(dest), urlsplit(src)
    h1s, h2s = us1.netloc.split(':')[0], us2.netloc.split(':')[0]
    seq1.append(h1s)
    seq2.append(h2s)
    p1s, p2s = us1.path, us2.path
    if p1s == '': p1s == '/'
    if p2s == '': p2s == '/'
    p1s, p2s = p1s.split('/'), p2s.split('/')
    seq1 += p1s[1:]
    seq2 += p2s[1:]
    diff = 0
    for i, (s1, s2) in enumerate(zip(seq1, seq2)):
        if s1 != s2: 
            diff += 1
            break
    diff += len(seq1) + len(seq2) - 2*(i+1)
    q1s, q2s = parse_qsl(us1.query), parse_qsl(us2.query)
    if diff == 0:
        diff += len(set(q1s).union(q2s)) - len(set(q1s).intersection(q2s))
    else:
        diff += min(len(q1s), len(set(q1s).union(q2s)) - len(set(q1s).intersection(q2s)))
    return diff


class Path:
    def __init__(self, url, link_sig=('', ('', '')), ss=None):
        self.url = url
        self.path = ss.path + [url] if ss else [url]
        self.sigs = ss.sigs + [link_sig] if ss else [link_sig]
        self.length = ss.length + 1 if ss else 0
    
    def from_dict(self, d):
        self.url = d['url']
        self.path = d['path']
        self.sigs = d['sigs']
        self.length = len(self.path)
    
    def calc_priority(self, dst, dst_rep, similar=None):
        """
        similar must be initialized to contain dst_rep and sigs
        """
        c1 = tree_diff(dst, self.url)
        c2 = len(self.path)
        if similar:
            anchor, sig = self.sigs[-1]
            simis = (similar.tfidf.similar(anchor, dst_rep), similar.tfidf.similar(' '.join(sig), dst_rep))
            simi = wsum_simi(simis)
            self.priority = c1 + c2 - simi
        else:
            self.priority = c1 + c2
        return self
    
    def __str__(self):
        d = {
            'url': self.url,
            'path': self.path
        }
        return json.dumps(d, indent=2)
    
    def to_dict(self):
        return {
            'url': self.url,
            'path': self.path,
            'sigs': self.sigs
        }


class Backpath_Finder:
    def __init__(self, policy='earliest', memo=None, similar=None):
        """
        Policy: earliest/latest
        """
        self.policy = policy
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar()
        policy_map = {
            'earliest': 'closest-later',
            'latest': 'closest-earlier'
        }
        self.memo_policy = policy_map[self.policy]

    def find_path(self, url, homepage=None):
        policy = self.policy
        trim_size = 20
        try:
            wayback_url = self.memo.wayback_index(url, policy=policy)
            html = self.memo.crawl(wayback_url)
            content = self.memo.extract_content(html, version='domdistiller')
            title = self.memo.extract_title(html, version='domdistiller')
            url_rep = title if title != '' else content
        except Exception as e:
            logger.error(f'Exceptions happen when loading wayback verison of url: {str(e)}') 
            html, title, content = '', '', ''
            us = urlsplit(url)
            url_rep = re.sub('[^0-9a-zA-Z]+', ' ', us.path)
            trim_size = 10
            if us.query:
                values = [u[1] for u in parse_qsl(us.query)]
                url_rep += f" {' '.join(values)}"
        # TODO Consider cases where there is no snapshot
        ts = url_utils.get_ts(wayback_url) if wayback_url else 20200101
        logger.info(f'ts: {ts}')
        us = urlsplit(url)
        homepage = urlunsplit(us._replace(path='', query='', fragment='')) if not homepage else homepage
        MAX_DEPTH = len(us.path.split('/')) + len(parse_qs(us.query))
        search_queue = [Path(homepage)]
        seen = set()
        param_dict = {
            "filter": ['statuscode:[23][0-9]*', 'mimetype:text/html'],
            "collapse": "timestamp:8"
        }
        while len(search_queue) > 0:
            path = search_queue.pop(0)
            logger.info(f'BackPath: {path.url} outgoing_queue:{len(search_queue)}')
            if len(path.path) > MAX_DEPTH or url_utils.url_norm(path.url) in seen:
                continue
            seen.add(url_utils.url_norm(path.url))
            if url_utils.url_match(url, path.url):
                return path
            wayback_url = self.memo.wayback_index(path.url, policy=self.memo_policy, ts=ts, param_dict=param_dict)
            logger.info(wayback_url)
            if wayback_url is None:
                continue
            wayback_html, wayback_url = self.memo.crawl(wayback_url, final_url=True)
            if wayback_html is None:
                continue
            outgoing_sigs = crawl.outgoing_links_sig(wayback_url, wayback_html, wayback=True)
            self.similar.tfidf._clear_workingset()
            corpus = [s[1] for s in outgoing_sigs] + [' '.join(s[2]) for s in outgoing_sigs] + [url_rep]
            self.similar.tfidf.add_corpus(corpus)
            for wayback_outgoing_link, anchor, sib_text in outgoing_sigs:
                outgoing_link = url_utils.filter_wayback(wayback_outgoing_link)
                new_path = Path(outgoing_link, link_sig=(anchor, sib_text), ss=path)
                if url_utils.url_match(wayback_outgoing_link, url, wayback=True):
                    return new_path
                if outgoing_link not in seen:
                    new_path.calc_priority(url, url_rep, self.similar)
                    seen.add(url_utils.url_norm(outgoing_link))
                    search_queue.append(new_path)
            search_queue.sort(key=lambda x: x.priority)
            search_queue = search_queue[:trim_size] if len(search_queue) > trim_size else search_queue
    
    def wayback_alias(self, url):
        """
        Utilize wayback's archived redirections to find the alias/reorg of the page

        Returns: reorg_url is latest archive is an redirection to working page, else None
        """
        param_dict = {
            "filter": ['statuscode:[23][0-9]*', 'mimetype:text/html'],
        }
        us = urlsplit(url)
        is_homepage = us.path in ['/', ''] and not us.query
        try:
            wayback_url = self.memo.wayback_index(url, policy='latest', param_dict=param_dict)
            _, wayback_url = self.memo.crawl(wayback_url, final_url=True)
            match = url_utils.url_match(url, url_utils.filter_wayback(wayback_url))
        except:
            return
        if not match:
            new_url = url_utils.filter_wayback(wayback_url)
            new_us = urlsplit(new_url)
            new_is_homepage = new_us.path in ['/', ''] and not new_us.query
            broken, reason = sic_transit.broken(new_url, html=True, ignore_soft_404=is_homepage and new_is_homepage)
            if not broken:
                return new_url
        return

    def find_same_link(self, link_sigs, liveweb_url, liveweb_html):
        """
        For same page from wayback and liveweb (still working). Find the same url from liveweb which matches to wayback
        
        Returns: If there is a match on the url, return sig, similarity, by
                 Else: return None
        """
        live_outgoing_sigs = crawl.outgoing_links_sig(liveweb_url, liveweb_html)
        for link_sig in link_sigs:
            matched_vals = self.similar.match_url_sig(link_sig, live_outgoing_sigs)
            if matched_vals is not None:
                return matched_vals
        return

    def match_path(self, path):
        """
        Match reorganized url with the prev given path
        Return: If hit, matched_urls, by 
        """
        o_pointer, i_pointer = 0, 0
        curr_url = path.path[o_pointer]
        matched = False
        while o_pointer < len(path.path) - 1:
            i_pointer = max(i_pointer, o_pointer)
            curr_url = path.path[o_pointer] if not matched else curr_url
            logger.info(f'curr_url: {curr_url} {matched}')
            curr_us = urlsplit(curr_url)
            is_homepage = curr_us.path in ['/', ''] and not curr_us.query
            broken, reason = sic_transit.broken(curr_url, html=True, ignore_soft_404=is_homepage)
            if broken: # Has to be not homepage to 
                new_url = self.wayback_alias(curr_url)
                if not new_url:
                    o_pointer += 1
                    matched = False
                    continue
                curr_url = new_url
            html = self.memo.crawl(curr_url)
            for compare in range(i_pointer+1, len(path.path)):
                link_match = self.find_same_link([(path.path[compare], path.sigs[compare][0], path.sigs[compare][1])], curr_url, html) # TODO, explicit list of wayback_sig mayn
                if link_match:
                    matched = True
                    matched_sig, simi, by = link_match
                    if compare == len(path.path) - 1: # Last path
                        if not sic_transit.broken(matched_sig[0])[0]:
                            return matched_sig[0], (f'link_{by}', simi)
                        else:
                            return
                    else:
                        curr_url = matched_sig[0]
                        i_pointer = compare
                        break
                else:
                    matched = False
            o_pointer += not matched
        return
            



class Discoverer:
    def __init__(self, depth=BUDGET, corpus=[], proxies={}, memo=None, similar=None):
        self.depth = depth
        self.corpus = corpus
        self.PS = crawl.ProxySelector(proxies)
        self.wayback = {} # {url: wayback ts}
        self.crawled = {} # {url: html}
        self.budget = BUDGET
        self.memo = memo if memo is not None else tools.Memoizer()
        self.similar = similar if similar is not None else tools.Similar()
        self.bf = Backpath_Finder(policy='latest', memo=self.memo, similar=self.similar)
    
    # def guess_backlinks(self, url):
    #     """
    #     Guess backlinks by returning:
    #         The parent url & If url with query, no query / partial query
    #     """
    #     MAX_GUESS = 32
    #     def powerset(iterable):
    #         "powerset([1,2,3]) --> () (1,) (2,) (3,) (1,2) (1,3) (2,3) (1,2,3)"
    #         s = list(iterable)
    #         return chain.from_iterable(combinations(s, r) for r in range(len(s)+1))
    #     us = urlsplit(url)
    #     path, query = us.path, us.query
    #     guessed_urls = []
    #     path_dir = os.path.dirname(path)
    #     if path != path_dir: # Not root dir
    #         us_tmp = us._replace(path=path_dir, query='')
    #         guessed_urls.append(urlunsplit(us_tmp))
    #     else: # Root dir, consider parent page as b.c for a.b.c
    #         hostname, site = us.netloc, he.extract(url)
    #         if hostname != site:
    #             hostname = '.'.join(hostname.split('.')[1:])
    #             us_tmp = us._replace(netloc=hostname, query='')
    #             guessed_urls.append(urlunsplit(us_tmp))
    #     if not query:
    #         return guessed_urls
    #     qsl = parse_qsl(query)
    #     if len(qsl) == 0:
    #         us_tmp = us._replace(query='')
    #         guessed_urls.append(urlunsplit(us_tmp))
    #         return guessed_urls
    #     for sub_q in powerset(qsl):
    #         if len(sub_q) == len(qsl): continue
    #         us_tmp = us._replace(query='&'.join([f'{kv[0]}={kv[1]}' for kv in sub_q]))
    #         guessed_urls.append(urlunsplit(us_tmp))
    #     if len(guessed_urls) > MAX_GUESS:
    #         guessed_urls = random.sample(guessed_urls, MAX_GUESS)
    #     return guessed_urls
    
    def guess_backlinks(self, url, num):
        """
        Retrieve closest neighbors for url archived by wayback
        num: Number of neighbors required

        # TODO: Add ts and url closeness into consideration
        """
        def closeness(url, cand):
            score = 0
            us, uc = urlsplit(url), urlsplit(cand)
            h1s, h2s = us.netloc.split(':')[0].split('.'), uc.netloc.split(':')[0].split('.')
            for h1, h2 in zip(reversed(h1s), reversed(h2s)):
                if h1 == h2: score += 1
                else: return score
            if len(h1s) != len(h2s):
                return score
            p1s, p2s = us.path, uc.path
            if p1s == '': p1s = '/'
            if p2s == '': p2s = '/'
            p1s, p2s = p1s.split('/')[1:], p2s.split('/')[1:]
            for p1, p2 in zip(p1s, p2s):
                if p1 == p2: score += 1
                else: break
            if len(p1s) != len(p2s):
                for _ in (len(p1s), len(p2s)): score -= 1
            q1s, q2s = parse_qs(us.query), parse_qs(uc.query)
            score += len(set(q1s.keys()).intersection(set(q2s.keys())))
            score -= max(0, len(q2s.keys()) - len( set(q1s.keys()).union( set(q2s.keys()) ) ))
            return score

        param_dict = {
            'from': 1997,
            'to': 2020,
            'filter': ['mimetype:text/html', 'statuscode:200'],
            'collapse': 'urlkey'
        }
        cands = []
        site = he.extract(url)
        us = urlsplit(url)
        path, query = us.path, us.query
        explicit_parent = url_utils.url_parent(url)
        if self.memo.wayback_index(explicit_parent, policy='earliest'):
            cands.append(explicit_parent)
        if path not in ['', '/'] or query:
            if path and path[-1] == '/': path = path[:-1]
            path_dir = os.path.dirname(path)
            q_url = urlunsplit(us._replace(path=path_dir + '*', query=''))
            wayback_urls, _ = crawl.wayback_index(q_url, param_dict=param_dict)
        elif us.netloc.split(':')[0] != site:
            hs = us.netloc.split(':')[0].split('.')
            hs[0] = '*'
            q_url = urlunsplit(us._replace(scheme='', path='', query=''))
            wayback_urls, _ = crawl.wayback_index(q_url, param_dict=param_dict)
        else:
            # TODO Think about this
            return []
        parents = [w[1] for w in wayback_urls if url_utils.is_parent(w[1], url) and \
             not url_utils.url_match(w[1], url) and not url_utils.is_parent(w[1], explicit_parent)]
        cands += parents
        closest_urls = [w[1] for w in wayback_urls if not url_utils.url_match(w[1], url) \
            and not url_utils.url_match(w[1], explicit_parent) and w[1] not in parents and self.loop_cand(url, w[1])]
        closest_urls.sort(key=lambda x: closeness(url, x), reverse=True)
        cands += closest_urls[:max(0, num-len(parents))] if len(closest_urls) > max(0, num-len(parents)) else closest_urls
        
        return cands

    def link_same_page(self, dst, title, content, backlinked_url, backlinked_html, cut=CUT):
        """
        See whether backedlinked_html contains links to the same page as html
        content: content file of the original url want to find copy
        backlinked_html: html which could be linking to the html
        cut: Max number of outlinks to test on. If set to <=0, there is no limit

        Returns: (link, similarity), from_where which is a copy of html if exists. None otherwise
        """
        if backlinked_url is None:
            return None, None
        backlinked_content = self.memo.extract_content(backlinked_html, version='domdistiller')
        backlinked_title = self.memo.extract_title(backlinked_html, version='domdistiller')
        similars, fromm = self.similar.similar(dst, title, content, {backlinked_url: backlinked_title}, {backlinked_url: backlinked_content})
        if len(similars) > 0:
            return similars[0], fromm

        # outgoing_links = crawl.outgoing_links(backlinked_url, backlinked_html, wayback=False)
        global he
        outgoing_sigs = crawl.outgoing_links_sig(backlinked_url, backlinked_html, wayback=False)
        outgoing_sigs = [osig for osig in outgoing_sigs if he.extract(osig[0]) == he.extract(dst)]
        if cut <= 0:
            cut = len(outgoing_sigs)
        if len(outgoing_sigs) > cut:
            repr_text = [title, content]
            self.similar.tfidf._clear_workingset()
            self.similar.tfidf.add_corpus([w[1] for w in outgoing_sigs] + [' '.join(w[2]) for w in outgoing_sigs] + repr_text)
            scoreboard = defaultdict(lambda: (0, 0))
            for outlink, anchor, sig in outgoing_sigs:
                simis = (self.similar.max_similar(anchor, repr_text, init=False)[0], self.similar.max_similar(' '.join(sig), repr_text, init=False)[0])
                scoreboard[outlink] = max(scoreboard[outlink], simis, key=lambda x: wsum_simi(x))
            scoreboard = sorted(scoreboard.items(), key=lambda x: wsum_simi(x[1]), reverse=True)
            outgoing_links = [sb[0] for sb in scoreboard[:cut]]
        else:
            outgoing_links = [osig[0] for osig in outgoing_sigs]

        # outgoing_contents = {}
        for outgoing_link in outgoing_links:
            if he.extract(dst) != he.extract(outgoing_link):
                continue
            html = self.memo.crawl(outgoing_link, proxies=self.PS.select())
            if html is None: continue
            logger.info(f'Test if outgoing link same: {outgoing_link}')
            outgoing_content = self.memo.extract_content(html, version='domdistiller')
            outgoing_title = self.memo.extract_title(html, version='domdistiller')
            similars, fromm = self.similar.similar(dst, title, content, {outgoing_link: outgoing_title}, {outgoing_link: outgoing_content})
            if len(similars) > 0:
                return similars[0], fromm
        return None, None
    
    def find_same_link(self, wayback_sigs, liveweb_url, liveweb_html):
        """
        For same page from wayback and liveweb (still working). Find the same url from liveweb which matches to wayback
        
        Returns: If there is a match on the url, return sig, similarity, by
                 Else: return None
        """
        live_outgoing_sigs = crawl.outgoing_links_sig(liveweb_url, liveweb_html)
        for wayback_sig in wayback_sigs:
            matched_vals = self.similar.match_url_sig(wayback_sig, live_outgoing_sigs)
            if matched_vals is not None:
                return matched_vals
        return
    
    def loop_cand(self, url, outgoing_url):
        """
        See whether outgoing_url is worth looping through
        Creteria: 1. Same domain 2. No deeper than url
        """
        global he
        if 'web.archive.org' in outgoing_url:
            outgoing_url = url_utils.filter_wayback(outgoing_url)
        if he.extract(url) != he.extract(outgoing_url):
            return False
        if urlsplit(url).path in urlsplit(outgoing_url).path and urlsplit(url).path != urlsplit(outgoing_url).path:
            return False
        return True

    def _wayback_alias(self, url):
        """
        Old version of wayback_alias, used for internal alias found
        Utilize wayback's archived redirections to find the alias/reorg of the page

        Returns: reorg_url is latest archive is an redirection to working page, else None
        """
        param_dict = {
            "filter": ['statuscode:[23][0-9]*', 'mimetype:text/html'],
        }
        us = urlsplit(url)
        is_homepage = us.path in ['/', ''] and not us.query
        try:
            wayback_url = self.memo.wayback_index(url, policy='latest', param_dict=param_dict)
            _, wayback_url = self.memo.crawl(wayback_url, final_url=True)
            match = url_utils.url_match(url, url_utils.filter_wayback(wayback_url))
        except:
            return
        if not match:
            new_url = url_utils.filter_wayback(wayback_url)
            new_us = urlsplit(new_url)
            new_is_homepage = new_us.path in ['/', ''] and not new_us.query
            if new_is_homepage and (not is_homepage): 
                return
            broken, reason = sic_transit.broken(new_url, html=True, ignore_soft_404=is_homepage and new_is_homepage)
            if not broken:
                return new_url
        return

    def wayback_alias(self, url):
        """
        Utilize wayback's archived redirections to find the alias/reorg of the page
        Not consider non-homepage to homepage
        If latest redirection is invalid, iterate towards earlier ones (separate by every month)

        Returns: reorg_url is latest archive is an redirection to working page, else None
        """
        param_dict = {
            "filter": ['statuscode:[23][0-9]*', 'mimetype:text/html'],
        }
        us = urlsplit(url)
        is_homepage = us.path in ['/', ''] and not us.query
        try:
            wayback_ts_urls = self.memo.wayback_index(url, policy='all', param_dict=param_dict)
        except: return

        if not wayback_ts_urls or len(wayback_ts_urls) == 0:
            return

        wayback_ts_urls = [(dparser.parse(c[0]), c[1]) for c in wayback_ts_urls]
        url_match_count = 0
        it = len(wayback_ts_urls) - 1
        last_ts = wayback_ts_urls[-1][0] + datetime.timedelta(days=90)
        seen_new_url = set()
        while url_match_count < 3 and it >= 0:
            ts, wayback_url = wayback_ts_urls[it]
            it -= 1
            if ts + datetime.timedelta(days=90) > last_ts: # 2 snapshots too close
                continue
            try:
                response = crawl.requests_crawl(wayback_url, raw=True)
                wayback_url = response.url
                match = url_utils.url_match(url, url_utils.filter_wayback(wayback_url))
            except:
                continue
            if not match:
                last_ts = ts
                new_url = url_utils.filter_wayback(wayback_url)
                if new_url in seen_new_url:
                    continue
                seen_new_url.add(new_url)
                inter_urls = [url_utils.filter_wayback(wu.url) for wu in response.history] # Check for multiple redirections
                inter_urls.append(new_url)
                inter_uss = [urlsplit(inter_url) for inter_url in inter_urls]
                logger.info(f'Wayback_alias: {ts}, {inter_urls}')
                new_is_homepage = True in [inter_us.path in ['/', ''] and not inter_us.query for inter_us in inter_uss]
                if new_is_homepage and (not is_homepage): 
                    continue
                broken, reason = sic_transit.broken(new_url, html=True, ignore_soft_404=is_homepage and new_is_homepage)
                if not broken:
                    return new_url
            else:
                url_match_count += 1
        return

    def discover_backlinks(self, src, dst, dst_title, dst_content, dst_html, dst_snapshot, dst_ts=None):
        """
        For src and dst, see:
            1. If src is archived on wayback
            2. If src is linking to dst on wayback
            3. If src is still working today
        
        dst_ts: timestamp for dst on wayback to reference on policy
                If there is no snapshot, use closest-latest
        
        returns: (status, url(s), reason), 
                    status includes: found/loop/reorg/notfound
                    url(s) are the urls of corresponding status. (found url, outgoing loop urls, etc)
                    reasons are how urls are found (from, similarity) or notfound (for no dst snapshots)
        """
        logger.info(f'Backlinks: {src} {dst}')
        policy = 'closest' if dst_ts else 'latest-rep'
        param_dict = {
            "filter": ['statuscode:[23][0-9]*', 'mimetype:text/html'],
            "collapse": "timestamp:8"
        }
        wayback_src = self.memo.wayback_index(src, policy=policy, ts=dst_ts, param_dict=param_dict)
        broken, reason = sic_transit.broken(src, html=True)
        # Directly check this outgoing page
        if not broken:
            src_html = self.memo.crawl(src)
            src_content = self.memo.extract_content(src_html, version='domdistiller')
            src_title = self.memo.extract_title(src_html, version='domdistiller')
            similars, fromm = self.similar.similar(dst, dst_title, dst_content, {src: src_title}, {src: src_content})
            if len(similars) > 0:
                logger.info(f'Discover: Directly found copy during looping')
                top_similar = similars[0]
                return "found", top_similar[0], (f'{fromm}', top_similar[1])

        if wayback_src is None: # No archive in wayback for guessed_url
            if not dst_snapshot:
                return "notfound", None, "Backlink no snapshots"
            if broken:
                logger.info(f'Discover backlinks broken: {reason}')
                return "notfound", None, None
            src_html, src = self.memo.crawl(src, final_url=True, max_retry=3)
            top_similar, fromm = self.link_same_page(dst, dst_title, dst_content, src, src_html, cut=10)
            if top_similar is not None:
                return "found", top_similar[0], (fromm, top_similar[1])
            else:
                return "notfound", None, None
        else:
            wayback_src_html, wayback_src = self.memo.crawl(wayback_src, final_url=True)
            wayback_outgoing_sigs = crawl.outgoing_links_sig(wayback_src, wayback_src_html, wayback=True)
            wayback_linked = [False, []]
            for wayback_outgoing_link, anchor, sibtext in wayback_outgoing_sigs:
                if url_utils.url_match(wayback_outgoing_link, dst, wayback=True):
                    # TODO: linked can be multiple links
                    wayback_linked[0] = True
                    wayback_linked[1].append((wayback_outgoing_link, anchor, sibtext))
            logger.info(f'Wayback linked: {wayback_linked[1]}')
            if broken:
                new_src = self._wayback_alias(src)
                if new_src:
                    broken = False
                    src = new_src 
            if wayback_linked[0] and not broken: # src linking to dst and is working today
                src_html, src = self.memo.crawl(src, final_url=True, max_retry=3)
                rval = self.find_same_link(wayback_linked[1], src, src_html)
                if rval:
                    matched_sig, simi, by = rval
                    if not sic_transit.broken(matched_sig[0])[0]:
                        return "found", matched_sig[0], (f'link_{by}', simi)
                    else:
                        return "notfound", None, "Linked, matched url broken"
                elif dst_snapshot: # Only consider the case for dst with snapshots
                    top_similar, fromm = self.link_same_page(dst, dst_title, dst_content, src, src_html)
                    if top_similar is not None: 
                        return "found", top_similar[0], (fromm, top_similar[1])
                    else:
                        return "notfound", None, "Linked, no matched link"
                else: # For dst without snapshots
                    return "notfound", None, "Linked, no matched link"
            elif not wayback_linked[0]: # Not linked to dst, need to look futher
                return "loop", wayback_outgoing_sigs, None
            else: # Linked to dst, but broken today
                return "reorg", wayback_outgoing_sigs, "Backlink broken today"

    def discover(self, url, depth=None, seen=None, trim_size=10):
        """
        Discover the potential reorganized site
        Trim size: The largest size of outgoing queue

        Return: If Found: URL, Trace (whether it information got suffice, how copy is found, etc)
                else: None, {'suffice': Bool, 'trace': traces}
        """
        if depth is None: depth = self.depth
        has_snapshot = False
        url_ts = None
        suffice = False # Only used for has_snapshot=False. See whehter url sufice restrictions. (Parent sp&linked&not broken today)
        traces = [] # Used for tracing the discovery process
        repr_text = [] # representitive text for url, composed with [title, content, url]
        ### First try with wayback alias
        try:
            wayback_url = self.memo.wayback_index(url, policy='latest-rep')
            html, wayback_url = self.memo.crawl(wayback_url, final_url=True)
            content = self.memo.extract_content(html, version='domdistiller')
            title = self.memo.extract_title(html, version='domdistiller')
            url_ts = url_utils.get_ts(wayback_url)
            has_snapshot = True
        except Exception as e:
            logger.error(f'Exceptions happen when loading wayback verison of url: {str(e)}') 
            html, title, content = '', '', ''
        suffice = has_snapshot or suffice

        # Get repr_text
        repr_text += [title, content]
        us = urlsplit(url)
        url_text = re.sub('[^0-9a-zA-Z]+', ' ', us.path)
        if us.query:
            values = [u[1] for u in parse_qsl(us.query)]
            url_text += f" {' '.join(values)}"
        repr_text.append(url_text)
        # End

        guess_total = defaultdict(int)
        g, curr_url = GUESS_DEPTH, url
        while g > 0:
            guessed_urls = self.guess_backlinks(curr_url, num=g)
            for gu in guessed_urls: guess_total[gu] = max(guess_total[gu], depth) # TODO depth can be changed with distance to url
            curr_url = url_utils.url_parent(curr_url)
            g -= 1

        seen = set() if seen is None else seen
        # seen.update(guessed_urls)

        guess_total = list(guess_total.items())

        outgoing_queue = []
        if has_snapshot: # Only loop to find backlinks when snapshot is available
            outgoing_sigs = crawl.outgoing_links_sig(wayback_url, html, wayback=True)
            self.similar.tfidf._clear_workingset()
            self.similar.tfidf.add_corpus([w[1] for w in outgoing_sigs] + [' '.join(w[2]) for w in outgoing_sigs] + repr_text)
            scoreboard = defaultdict(int)
            for outlink, anchor, sig in outgoing_sigs:
                outlink = url_utils.filter_wayback(outlink)
                # For each link, find highest link score
                if outlink not in seen and self.loop_cand(url, outlink):
                    simis = (self.similar.max_similar(anchor, repr_text, init=False)[0], self.similar.max_similar(' '.join(sig), repr_text, init=False)[0])
                    spatial = tree_diff(url, outlink)
                    scoreboard[outlink] = max(scoreboard[outlink], estimated_score(spatial, simis))
            for outlink, score in scoreboard.items():
                outgoing_queue.append((outlink, depth-OUTGOING, score))

            outgoing_queue.sort(key=lambda x: x[2])
            outgoing_queue = outgoing_queue[: trim_size+1] if len(outgoing_queue) >= trim_size else outgoing_queue

        while len(guess_total) + len(outgoing_queue) > 0:
            # Ops for guessed links
            two_src = []
            if len(guess_total) > 0:
                two_src.append(guess_total.pop(0))
            if len(outgoing_queue) > 0:
                two_src.append(outgoing_queue.pop(0)[:2])
            for item in two_src:
                src, link_depth = item
                logger.info(f"Got: {src} depth:{link_depth} guess_total:{len(guess_total)} outgoing_queue:{len(outgoing_queue)}")
                if src in seen:
                    continue
                seen.add(src)
                status, msg_urls, reason = self.discover_backlinks(src, url, title, content, html, has_snapshot, url_ts)
                logger.info(f'{status}, {reason}')
                if status == 'found':
                    traces.append({
                        "backlink": src,
                        "status": "found"
                    })
                    return msg_urls, {'suffice': True, 'type': reason[0], 'value': reason[1], 'trace': traces}
                elif status == 'loop':
                    traces.append({
                        "backlink": src,
                        "status": "loop"
                    })
                    if link_depth >= OUTGOING:
                        scoreboard = defaultdict(int)
                        self.similar.tfidf._clear_workingset()
                        self.similar.tfidf.add_corpus([w[1] for w in msg_urls] + [' '.join(w[2]) for w in msg_urls] + repr_text)
                        for outlink, anchor, sig in msg_urls:
                            outlink = url_utils.filter_wayback(outlink)
                            # For each link, find highest link score
                            if outlink not in seen and self.loop_cand(url, outlink):
                                simis = (self.similar.max_similar(anchor, repr_text, init=False)[0], self.similar.max_similar(' '.join(sig), repr_text, init=False)[0])
                                spatial = tree_diff(url, outlink)
                                scoreboard[outlink] = max(scoreboard[outlink], estimated_score(spatial, simis))
                        for outlink, score in scoreboard.items():
                            outgoing_queue.append((outlink, link_depth-OUTGOING, score))
                elif status in ['notfound', 'reorg']:
                    traces.append({
                        "backlink": src,
                        "status": status,
                        "reason": reason
                    })
                    if not has_snapshot:
                        suffice = suffice or 'Linked' in reason
            
            outgoing_queue.sort(key=lambda x: x[2])
            dedup,uniq_q, uniq_c = set(), [], 0
            while len(dedup) < trim_size and uniq_c < len(outgoing_queue):
                if outgoing_queue[uniq_c][0] not in dedup:
                    dedup.add(outgoing_queue[uniq_c][0])
                    uniq_q.append(outgoing_queue[uniq_c])
                uniq_c += 1
            outgoing_queue = uniq_q
            # elif status == 'reorg':
            #     reorg_src = self.discover(src, depth=depth, seen=seen)
            #     if reorg_src is not None and reorg_src not in seen:
            #         search_queue.put((reorg_src, depth))
            #         seen.add(reorg_src)
        return None, {'suffice': suffice, 'trace': traces}

    def bf_find(self, url, policy='latest'):
        """
        Return: If Hit, reorg_url, {'suffice': , 'type': , 'value': , 'backpath': path}
                Else: None, {'suffice': False, 'backpath': depends} 
        """
        self.bf.policy = policy
        if urlsplit(url).path in {'', '/'}:
            return None, {'suffice': False}
        path = self.bf.find_path(url)
        if not path:
            return None, {'suffice': False}
        logger.info(f'backpath: {path}')
        rval = self.bf.match_path(path)
        if rval:
            reorg_url, (typee, value) = rval
            return reorg_url, {'suffice': True, 'type': typee, 'value': value, 'backpath': path.to_dict()}
        else:
            return None, {'suffice': True, 'backpath': path.to_dict()}
        