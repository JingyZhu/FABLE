import logging
from fable import ReorgPageFinder

urls = ['http://www.espnfc.com/story/3051411/hiroshi-kiyotake-sold-by-sevilla-to-former-side-cerezo-osaka']
rpf = ReorgPageFinder(classname='achitta', logname='achitta', loglevel=logging.DEBUG)
rpf.init_site('espnfc.com', urls)
rpf.search(required_urls=urls)
rpf.discover(required_urls=urls)