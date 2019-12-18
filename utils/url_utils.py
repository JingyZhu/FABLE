
from publicsuffix import fetch, PublicSuffixList
from bs4 import BeautifulSoup
from urllib.parse import urlparse

class HostExtractor:
    def __init__(self):
        self.psl = PublicSuffixList(fetch())
    
    def extract(self, url, wayback=False):
        """
        Wayback: Whether the url is got from wayback
        """"
        if wayback:
            last_https = url.rfind('https://')
            last_http = url.rfind('http://')
            idx = max(last_http, last_https)
            url = url[idx:]
        if 'http://' not in url or 'https://' not in url:
            url = 'http://' + url
        return self.psl.get_public_suffix(urlparse(url).netloc)


def filter_separator(string):
    separator = [' ', '\n']
    for sep in separator:
        string = string.replace(sep, '')
    return string


def find_link_density(html):
    """
    Find link density of a webpage given html
    """
    soup = BeautifulSoup(html, 'html.parser')
    filter_tags = ['style', 'script']
    for tag in filter_tags:
        for element in soup.findAll(tag):
            element.decompose()
    total_text = filter_separator(soup.get_text())
    total_length = len(total_text)
    atag_length = 0
    for atag in soup.findAll('a'):
        atag_text = filter_separator(atag.get_text())
        atag_length += len(atag_text)

    return atag_length / total_length
