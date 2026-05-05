# dominiopublico.gov.br — fixtures

Synthesized from a single browser inspection of the live site on 2026-05-04.
Every structural choice here mirrors what the real catalog page exposes; values
were taken from a real `pagina=1` capture (Literatura + Português + Texto)
showing 2.079 itens.

The site is behind Cloudflare managed challenge (`cf-mitigated: challenge`),
so production scraping uses Playwright headless. Tests do NOT hit the network —
they exercise the parser on these static files.

## Files

- `page_001.html` — populated result page, 8 representative rows preserving the
  observed quirks (titles with quotes/punctuation, authors with accents,
  diverse `[xx] Source` codes, sizes in both KB and MB, the "1 ." numbering).
- `page_overflow.html` — same `co_obra` set as `page_001.html`. Models the
  observed legacy-pager behavior: requesting a `pagina=` past the last real
  page returns the page=1 result set instead of an empty one.
- `page_malformed.html` — one well-formed row plus one row with an empty title
  and one row whose title cell has no link. Parser must skip the bad rows and
  return the good one.
- `robots_disallow.txt` — synthetic robots.txt that disallows `/pesquisa/`.
  Used by the `respects_robots_txt` test.

## Encoding

The live site declares `charset=iso-8859-1` in its `<meta http-equiv>`. These
fixtures are saved as UTF-8 (the parser handles both via BeautifulSoup's
declared-charset detection); the meta tag is preserved so the parser's
charset-handling path is exercised.
