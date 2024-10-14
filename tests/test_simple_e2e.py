"""
Simple test file to test the functionality of the fable module.
"""
from fable import fable, tools
import json
import logging

results = []
candidates = []

simi = tools.Similar()
alias_finder = fable.AliasFinder(similar=simi, classname=f'test', loglevel=logging.INFO)
netloc_urls = [('sup.org', [
      "http://www.sup.org/book.cgi?id=22655",
      "http://www.sup.org/book.cgi?id=21682",
      "http://www.sup.org/book.cgi?id=7994"
    ])]

for netloc, urls in netloc_urls:
    try:
        aliases = alias_finder.run_order(netloc, urls)
        netloc_cands = {'netloc_dir': netloc}
        c = alias_finder._candidate_cache.get(netloc, \
            {'search': [], 'hist_redir': [], 'redirection': [], 'inference': []})
        netloc_cands.update(c)
        candidates.append(netloc_cands)
        print("Candidates:", json.dumps(candidates, indent=2))
    except Exception as e:
        aliases = []
        print("ERROR:", netloc, e)
    results.append({
            'netloc_dir': netloc,
            'alias': aliases
        })
print("Aliases", json.dump(results, indent=2))