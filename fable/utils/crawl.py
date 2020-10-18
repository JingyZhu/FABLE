"""
Utilities for crawling a page
"""
from subprocess import call, check_output
import requests 
import os
import time
from os.path import abspath, dirname, join
import base64
import threading, queue
import itertools
import cchardet
from urllib.parse import urlparse, urljoin
import json
from collections import defaultdict
from itertools import product
from bs4 import BeautifulSoup
import bs4

from urllib.robotparser import RobotFileParser
from reppy.robots import Robots
from reppy.cache import RobotsCache
from reppy.ttl import HeaderWithDefaultPolicy
import sys

sys.path.append('../')
from .. import config
from .url_utils import filter_wayback

import logging
logger = logging.getLogger('logger')

requests_header = {'user-agent': config.config('user_agent')}
CRAWL_DELAY = 3

class ProxySelector:
    """
    Select Proxy from a pool
    """
    def __init__(self, proxies):
        """
        proxies: A list of available proxies that in the format of requests.get proxy input
        """
        if proxies is None or len(proxies) == 0:
            proxies = [{}]
        self.proxies = proxies
        self.len = len(proxies)
        self.idx = 0

    def select(self):
        """ Currently RR """
        self.idx = self.idx + 1 if self.idx < self.len -1 else 0
        return self.proxies[self.idx]
    
    def select_url(self, scheme='http'):
        """ Directly return url instead of dict """
        proxy = self.select()
        if proxy == {}: return
        else: return "{}://{}".format(scheme, proxy[scheme] )

class RobotParser:
    """
    Logic related to Robot
        - Get robots.txt and cache it.
        - Properly delay for specified crawl_delay 
    """
    def __init__(self, useragent=requests_header['user-agent']):
        policy = HeaderWithDefaultPolicy(default=3600, minimum=600)
        self.useragent = useragent
        self.rp = RobotsCache(capacity=1000, ttl_policy=policy, headers=requests_header, timeout=10)
        self.last_request = defaultdict(int) # {hostname: last request ts}
        self.req_status = {} # Robot url: status_code/'error'

    def allowed(self, url, useragent=None):
        if config.config('user_agent') != self.useragent:
            self.useragent = config.config('user_agent')
        if useragent is None: useragent = self.useragent
        self.rp.kwargs['headers'] = {'user-agent': useragent}
        scheme, netloc = urlparse(url).scheme, urlparse(url).netloc
        robot_url = f'{scheme}://{netloc}/robots.txt'

        # reppy consider 403, 500 as disallow_all. Overwriting this rule
        if robot_url not in self.req_status:
            try:
                r = requests.get(robot_url, timeout=5, headers={'user-agent': useragent})
                self.req_status[robot_url] = r.status_code
            except:
                self.req_status[robot_url] = 'error'
        if self.req_status[robot_url] == 'error' or self.req_status[robot_url] >= 400 :
            return True

        allow = self.rp.allowed(url, useragent)
        if allow:
            delay = self.rp.get(url).agent(useragent).delay
            if delay is None: return allow
            delay = min(CRAWL_DELAY, delay)
            diff = time.time() - self.last_request[netloc]
            if delay > diff: time.sleep(delay - diff)
            self.last_request[netloc] = time.time()
        return allow

rp = RobotParser()


def chrome_crawl(url, timeout=120, screenshot=False, ID=''):
    """
    Use chrome to load the page. Directly return the HTML text
    ID: If multi-threaded, should give ID for each thread to differentiate temp file
    """
    try:
        cur = str(int(time.time())) + '_' + str(os.getpid()) + ID
        file = cur + '.html'
        cmd = ['node', join(dirname(abspath(__file__)), 'run.js'), url, '--filename', cur]
        if screenshot:
            cmd.append('--screenshot')
        call(cmd, timeout=timeout)
    except Exception as e:
        print(str(e))
        pid = open(file, 'r').read()
        call(['kill', '-9', pid])
        os.remove(file)
        return "" if not screenshot else "", ""
    html = open(file, 'r').read()
    os.remove(file)
    if not screenshot:
        return html

    img = open(cur + '.jpg', 'r').read()
    os.remove(cur + '.jpg')
    url_file = url.replace('http://', '')
    url_file = url_file.replace('https://', '')
    url_file = url_file.replace('/', '-')
    f = open(url_file + '.jpg', 'wb+')
    f.write(base64.b64decode(img))
    f.close()
    return html, url_file + 'jpg'


def wayback_index(url, param_dict={}, wait=True, total_link=False, proxies={}):
    """
    Get the wayback machine index of certain url by querying the CDX
    wait: wait unitl not getting block
    total_link: Returned url are in full(wayback) links

    return: ( [(timestamp, url, stauts_code)], SUCCESS/EMPTY/ERROR_MSG)
    """
    wayback_home = 'http://web.archive.org/web/'
    params = {
        'output': 'json',
        'url': url,
        'from': 19700101,
        'to': 20201231,
    }
    params.update(param_dict)
    count = 0
    while True:
        try:
            r = requests.get('http://web.archive.org/cdx/search/cdx', headers=requests_header, params=params, proxies=proxies)
            r = r.json()
            break
        except Exception as e:
            try:
                logger.warn(f"Wayback index: {str(e)}" + '\n'  + r.text.split('\n')[0])
            except Exception as e:
                count += 1
                if count < 3:
                    time.sleep(20)
                    continue 
                return [], str(e)
            if not wait or r.status_code not in [429, 445, 503]:
                return [], str(e)
            time.sleep(10)
    if total_link:
        r = [(i[1], f"{wayback_home}{i[1]}/{i[2]}", i[4]) for i in r[1:]]
    else:
        r = [(i[1], i[2], i[4]) for i in r[1:]]
    if len(r) != 0:
        return r, "Success",
    else:
        return [], "Empty"


def wayback_year_links(prefix, years, NUM_THREADS=3, max_limit=0, param_dict={}, proxies={}):
    """
    Get the result of links in certain years
    prefix: some string of url e.g: *.a.b.com/*
    years: list of years which would be query
    max_limit: Maximum #records in one retrieval
    params: Any customized params, except time range

    Should be add in try catch. In case of connection error
    """
    total_r = {}
    cur_limit = 100000 if max_limit == 0 else max_limit
    wayback_home = 'http://web.archive.org/web/'
    params = {
        'output': 'json',
        'url': prefix,
        "limit": str(cur_limit),
        'collapse': 'urlkey',
        'filter': ['statuscode:200', 'mimetype:text/html'],
    }
    params.update(param_dict)
    l = threading.Lock()
    def get_year_links(q_in):
        nonlocal total_r, cur_limit, max_limit
        while not q_in.empty():
            year = q_in.get()
            total_r.setdefault(year, set())
            params.update({
                "from": "{}0101".format(year),
                "to": "{}1231".format(year)
                # 'collapse': 'timestamp:4',
            })
            
            while True:
                try:
                    r = requests.get('http://web.archive.org/cdx/search/cdx', params=params, proxies=proxies)
                    r = r.json()
                    r = [u[2] for u in r[1:]]
                except Exception as e:
                    print('1', str(e))
                    time.sleep(10)
                    continue
                try:
                    assert(len(r) < cur_limit or cur_limit >= max_limit)
                    break
                except Exception as e:
                    print('2', str(e))
                    cur_limit *= 2
                    params.update({'limit': str(cur_limit)})
                    continue
            print( (year, len(r)) )
            l.acquire()
            for url in r:
                total_r[year].add(url)
            l.release()
    t = []
    q_in = queue.Queue(maxsize=len(years) + 1)
    for year in years:
        q_in.put(year)
    for _ in range(NUM_THREADS):
        t.append(threading.Thread(target=get_year_links, args=(q_in,)))
        t[-1].start()
    for tt in t:
        tt.join() 

    return {k: list(v) for k, v in total_r.items()}


def requests_crawl(url, timeout=20, wait=True, html=True, proxies={}, raw=False):
    """
    Use requests to get the page
    Return None if fails to get the content
    html: Only return html if set to true
    wait: Will wait if get block
    raw: Return raw response instead of html if set to True

    Return:
        If good crawl: str/response
        Elif bad crawl: None
        Else (not applicable): (None, Reason)
    """
    requests_header = {'user-agent': config.config('user_agent')}
    filter_ext = ['.pdf']
    if os.path.splitext(url)[1] in filter_ext: 
        return None, 'Filtered ext'
    count = 0
    if not rp.allowed(url, requests_header['user-agent']):
        return None, "Not Allowed by Robot.txt"
    while True:
        try:
            r = requests.get(url, timeout=timeout, proxies=proxies, headers=requests_header)
            if wait and (r.status_code == 429 or r.status_code == 504) and count < 3:  # Requests limit
                logger.info(f'requests_crawl: get status code {r.status_code}')
                count += 1
                time.sleep(10)
                continue
            break
        except Exception as e:
            logger.warn(f"There is an exception with requests_crawl: {str(e)}")
            return
    if r.status_code >= 400:
        if r.status_code == 403: logger.info(f'requests_crawl: Get status code 403')
        return
    headers = {k.lower(): v.lower() for k, v in r.headers.items()}
    content_type = headers['content-type'] if 'content-type' in headers else ''
    if html and 'html' not in content_type:
        logger.info('requests_crawl: No html in content-type')
        return
    r.encoding = r.apparent_encoding
    if raw:
        return r
    else:
        return r.text


def get_sitemaps(hostname):
    """
    Trying to find the sitemap of a site
    TODO Iterate over sitemap trees to find all the urls
    """
    requests_header = {'user-agent': config.config('user_agent')}
    try:
        r = requests.get('http://{}/'.format(hostname), headers=requests_header, timeout=10)
    except: return None
    hostname = urlparse(r.url).netloc
    robots_url = 'http://{}/robots.txt'.format(hostname)
    try:
        rp = Robots.fetch(robots_url, headers=requests_header, timeout=10)
        sitemaps = rp.sitemaps
    except: sitemaps = []
    if len(sitemaps) > 0: return sitemaps
    sitemap_url = 'http://{}/sitemap.xml'.format(hostname)
    try:
        r = requests.get(sitemap_url, headers=requests_header, timeout=10)
        if r.status_code >= 400: return None
        else: return [sitemap_url]
    except: return None


def wappalyzer_analyze(url, proxy=None, timeout=None):
    """
    Use wappalyzer to analyze the tech used by this website
    Timeout: Time for pages to load resources, in ms
    """
    agent_string = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36"
    focus_categories = {
        "1": "CMS", 
        "18": "Web frameworks",
        "22": "Web servers", 
        "27": "Programming Languages", 
        # "28": "Operating Systems",
        "34": "Databases", 
        "62": "Paas",
        "64": "Reverse proxies"
    }
    count = 0
    while True:
        try:
            r = requests.get(url, timeout=timeout, headers=requests_header)
            if (r.status_code == 429 or r.status_code == 504) and count < 3:  # Requests limit
                print('requests_crawl:', 'get status code', str(r.status_code))
                count += 1
                time.sleep(10)
                continue
            break
        except Exception as e:
            print("There is an exception with requests crawl:",str(e))
            return
    url = r.url if r.status_code / 100 < 4 else url
    tech = defaultdict(list)
    flags = {'-a': agent_string}
    if proxy: flags.update({'--proxy': proxy})
    if timeout: flags.update({'-w': timeout*1000})
    flags_cmd = sum([[k, str(v)] for k, v in flags.items()], [])
    browsers = ['zombie', 'puppeteer']
    for browser in browsers:
        try:
            cmd = ['wappalyzer', '-b', browser] + flags_cmd + [url]
            output = check_output(cmd, timeout=20)
            result = json.loads(output.decode())
        except Exception as e:
            print('Wappalyzer in crawl:', str(e))
            continue
        if len(result['applications']) > 0: break
        print('continue', browser, url)
    for obj in result['applications']:
        for cate in obj['categories']:
            key = list(cate.keys())[0]
            if key in focus_categories:
                tech[focus_categories[key]].append(obj['name'])
    return tech


def outgoing_links(url, html, wayback=False):
    """
    Given the html, return all the outgoing links
    wayback: Whether the page is crawled from wayback
    """
    def wayback_join(url, link):
        link = urljoin(url, link)
        link = link.replace('http:/', 'http://')
        link = link.replace('http:///', 'http://')
        link = link.replace('https:/', 'https://')
        link = link.replace('https:///', 'https://')
        return link
    outlinks = set()
    try:
        soup = BeautifulSoup(html, 'lxml')
    except:
        logger.warn("Failed to construct soup")
        return []
    if wayback:
        # Filter out navigational part
        wm_ipp = soup.find_all('div', id='wm-ipp-base')
        if len(wm_ipp) > 0: wm_ipp[0].decompose()
        donato = soup.find_all('div', id='donato')
        if len(donato) > 0: donato[0].decompose()
    
    base = soup.find('base')
    base_url = url if base is None else urljoin(url, base.get('href'))

    for a_tag in soup.find_all('a'):
        if 'href' not in a_tag.attrs or a_tag.text.strip() == '':
            continue
        link = a_tag.attrs['href']
        if len(link) == 0 or link[0] == '#': #Anchor ignore
            continue
        if wayback:
            link = wayback_join(base_url, link)
        else:
            link = urljoin(base_url, link)
        if urlparse(filter_wayback(link)).scheme not in {'http', 'https'}:
            continue
        outlinks.add(link)
    outlinks = list(outlinks)
    # TODO: Add form outgoing tags
    # for form_tag in soup.find_all('tag'):

    return outlinks


def outgoing_links_sig(url, html, wayback=False):
    """
    Given the html, return all the (outgoing links, anchor text, signature) pairs
    wayback: Whether the page is crawled from wayback
    """
    def wayback_join(url, link):
        link = urljoin(url, link)
        link = link.replace('http:/', 'http://')
        link = link.replace('http:///', 'http://')
        link = link.replace('https:/', 'https://')
        link = link.replace('https:///', 'https://')
        return link
    outsigs = set()
    try:
        soup = BeautifulSoup(html, 'lxml')
    except:
        logger.warn("Failed to construct soup")
        return []
    if wayback:
        # Filter out navigational part
        wm_ipp = soup.find_all('div', id='wm-ipp-base')
        if len(wm_ipp) > 0: wm_ipp[0].decompose()
        donato = soup.find_all('div', id='donato')
        if len(donato) > 0: donato[0].decompose()

    base = soup.find('base')
    base_url = url if base is None else urljoin(url, base.get('href'))

    for a_tag in soup.find_all('a'):
        if 'href' not in a_tag.attrs or a_tag.text.strip() == '':
            continue
        link = a_tag.attrs['href']
        anchor_text = a_tag.text.strip()
        if len(link) == 0 or link[0] == '#': #Anchor ignore
            continue
        if wayback:
            link = wayback_join(base_url, link)
        else:
            link = urljoin(base_url, link)
        if urlparse(filter_wayback(link)).scheme not in {'http', 'https'}:
            continue
        # Get parent 
        par, child = a_tag, a_tag
        count = 0# Prevent dead loop
        while par and par.text.strip() == a_tag.text.strip() and count < 100:
            child = par
            par = par.parent
            count += 1

        sig = []
        prev_tag = child.find_previous_sibling()
        prev_str = child.previous_sibling
        prev_tag = prev_tag.get_text(separator=' ').strip() if prev_tag is not None else None
        if isinstance(prev_str, bs4.element.NavigableString):
            prev_str = prev_str.strip() if prev_str.strip() != '' else None
        else:
            prev_str = prev_str.get_text(separator=' ').strip() if prev_str is not None else None
        if prev_tag is not None and prev_str is not None:
            whole_text = par.get_text(separator=' ')
            if whole_text.find(prev_tag) > whole_text.find(prev_str):
                sig.append(prev_tag)
            else:
                sig.append(prev_str)
        elif prev_tag is not None : 
            sig.append(prev_tag)
        elif prev_str is not None:
            sig.append(prev_str)
        
        next_tag = child.find_next_sibling()
        next_str = child.next_sibling
        next_tag = next_tag.get_text(separator=' ').strip() if next_tag is not None else None
        if isinstance(next_str, bs4.element.NavigableString):
            next_str = next_str.strip() if next_str.strip() != '' else None
        else:
            next_str = next_str.get_text(separator=' ').strip() if next_str is not None else None
        if next_tag is not None and next_str is not None:
            whole_text = par.get_text(separator=' ')
            if whole_text.find(next_tag) < whole_text.find(next_str):
                sig.append(next_tag)
            else:
                sig.append(next_str)
        elif next_tag is not None : 
            sig.append(next_tag)
        elif next_str is not None:
            sig.append(next_str)

        sig = tuple(sig)
        outsigs.add((link, anchor_text, sig))
    outsigs = list(outsigs)
    # TODO: Add form outgoing tags
    # for form_tag in soup.find_all('tag'):

    return outsigs