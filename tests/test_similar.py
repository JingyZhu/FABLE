import pytest
import logging
import os

from fable import tools, tracer, config
from fable.utils import url_utils

he = url_utils.HostExtractor()
memo = tools.Memoizer()
simi = None
db = config.DB
tr = None

def _init_large_obj():
    global simi, search, tr
    if tr is None:
        try:
            os.remove(os.path.basename(__file__).split(".")[0] + '.log')
        except: pass
        logging.setLoggerClass(tracer.tracer)
        tr = logging.getLogger('logger')
        logging.setLoggerClass(logging.Logger)
        tr._unset_meta()
        tr._set_meta(os.path.basename(__file__).split(".")[0], db=db, loglevel=logging.DEBUG)
    if simi is None:
        simi = tools.Similar()

unsolved = {
    # ! Domdistiller is getting title from <meta "title"> instead of the actual title
    "https://www.archives.gov/exhibits/featured_documents/emancipation_proclamation/": True
}

def test_is_title_unique_notunique():
    _init_large_obj()
    urls = {
        "http://xenon.stanford.edu/~xusch/regexp/analyzer.html": True
    }
    for url, wayback in urls.items():
        site = he.extract(url)
        simi._init_titles(site)
        if wayback:
            target_url = memo.wayback_index(url)
        else:
            target_url = url
        html = memo.crawl(target_url)
        title = memo.extract_title(html)
        is_unique = simi._is_title_unique(target_url, title, content='', wayback=wayback)
        assert(is_unique == False)


def test_is_title_unique():
    _init_large_obj()
    urls = {
        "https://www.archives.gov/exhibits/featured_documents/emancipation_proclamation/": True
    }
    for url, wayback in urls.items():
        site = he.extract(url)
        simi._init_titles(site)
        if wayback:
            target_url = memo.wayback_index(url)
        else:
            target_url = url
        html = memo.crawl(target_url)
        title = memo.extract_title(html)
        is_unique = simi._is_title_unique(target_url, title, content='', wayback=wayback)
        assert(is_unique == False)

def test_unique_title():
    _init_large_obj()
    urls = {
        "http://www.wiley.com:80/cda/product/0,,0471357278,00.html": (True, "Principles of Molecular Mechanics"),
        "http://arduino.cc:80/forum/index.php?action=quickmod2;topic=48342.0": (True, "")
    }
    for url, (wayback, uniq_title) in urls.items():
        site = he.extract(url, wayback=wayback)
        simi._init_titles(site)
        if wayback:
            target_url = memo.wayback_index(url)
        else:
            target_url = url
        html = memo.crawl(target_url)
        title = memo.extract_title(html)
        meta = simi.wb_meta if wayback else simi.lw_meta
        found_uniq_title = simi.unique_title(target_url, title, '', meta, wayback=wayback)
        assert(found_uniq_title == uniq_title)

def test_unique_title_temp():
    _init_large_obj()
    urls = {
        "http://arduino.cc:80/forum/index.php?action=quickmod2;topic=48342.0": (True, "An Error Has Occurred!")
    }
    for url, (wayback, uniq_title) in urls.items():
        site = he.extract(url, wayback=wayback)
        simi._init_titles(site)
        if wayback:
            target_url = memo.wayback_index(url)
        else:
            target_url = url
        html = memo.crawl(target_url)
        title = memo.extract_title(html)
        more_crawls = memo.get_more_crawls(target_url, wayback=wayback)
        for more_crawl in more_crawls:
            simi._add_crawl(more_crawl['url'], more_crawl['title'], more_crawl['content'], more_crawl['html'])
        meta = simi.wb_meta if wayback else simi.lw_meta
        found_uniq_title = simi.unique_title(target_url, title, '', meta, wayback=wayback)
        assert(found_uniq_title == uniq_title)

# test_is_title_unique_temp()