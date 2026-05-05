"""Stage 1 — catalog scraper for dominiopublico.gov.br (Literatura, PT, PDF).

Walks the public-domain catalog page by page, persists per-work metadata to
``metadata.sqlite``, and exits. No PDF downloads (Stage 2), no triage,
no extraction. Idempotent — `INSERT OR IGNORE` keyed on ``catalog_url``.

The site is fronted by Cloudflare managed challenge (`cf-mitigated: challenge`),
so plain ``httpx`` GETs always return 403. We use Playwright headless Chromium
which executes the challenge JS and obtains a ``cf_clearance`` cookie. The
parser is then pure ``BeautifulSoup`` over the rendered HTML and is exercised
by ``tests/test_scrape_catalog.py`` against static fixtures.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import unicodedata
import urllib.robotparser
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from pindorama import db, paths

LOG = logging.getLogger("pindorama.scrape_catalog")

BASE_URL = (
    "https://dominiopublico.gov.br/pesquisa/ResultadoPesquisaObraForm.do"
    "?first=50&skip=0&ds_titulo=&co_autor=&no_autor=&co_categoria=2"
    "&pagina={page}&select_action=Submit&co_midia=2&co_obra=&co_idioma=1"
    "&colunaOrdenar=null&ordem=null"
)
ROBOTS_URL = "https://dominiopublico.gov.br/robots.txt"
SEARCH_PATH = "/pesquisa/ResultadoPesquisaObraForm.do"
USER_AGENT = "Pindorama-Research-Bot/1.0 (research project, contact: dame9177@colorado.edu)"
RATE_LIMIT_PER_SECOND = 2.0
PAGE_LOAD_TIMEOUT_MS = 30_000
RESULT_LINK_SELECTOR = 'a[href*="DetalheObraForm.do"]'


@dataclass(frozen=True)
class CatalogRow:
    """One parsed work row, ready for ``INSERT OR IGNORE INTO works``."""

    catalog_url: str
    title: str
    author: str | None
    year: int | None
    raw_metadata_json: str
    scraped_at: str


class RateLimiter:
    """Token-bucket-ish gate. ``acquire()`` blocks so calls happen ≤ rate/s.

    The clock and sleep are injectable so tests can run with a fake clock and
    assert timing without real sleeps.
    """

    def __init__(
        self,
        rate_per_second: float,
        *,
        now_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._min_gap = 1.0 / rate_per_second
        self._now_fn = now_fn
        self._sleep_fn = sleep_fn
        self._next_allowed: float = now_fn()

    def acquire(self) -> None:
        now = self._now_fn()
        wait = self._next_allowed - now
        if wait > 0:
            self._sleep_fn(wait)
            now = self._now_fn()
        self._next_allowed = now + self._min_gap


def _normalize_text(s: str) -> str:
    return unicodedata.normalize("NFC", " ".join(s.split()))


def _extract_co_obra(href: str, base: str) -> str | None:
    try:
        absolute = urljoin(base, href)
        params = parse_qs(urlparse(absolute).query)
    except (ValueError, AttributeError):
        return None
    co_obra = params.get("co_obra", [None])[0]
    if not co_obra or not co_obra.isdigit():
        return None
    return co_obra


def _canonical_catalog_url(co_obra: str) -> str:
    return f"https://dominiopublico.gov.br/pesquisa/DetalheObraForm.do?co_obra={co_obra}"


def _maybe_year_from_text(*texts: str) -> int | None:
    """Pull a 4-digit year from any of the given texts. Returns None if none found.

    Defensive: dominiopublico's literature listings rarely embed a year, and
    the field is always ``None`` in practice. Kept so the schema column has
    a deterministic source if a future row happens to include "(1885)".
    """
    for t in texts:
        for tok in t.replace("(", " ").replace(")", " ").split():
            if len(tok) == 4 and tok.isdigit():
                year = int(tok)
                if 1300 <= year <= 2100:
                    return year
    return None


def parse_catalog_page(html: str, page: int, scraped_at: str) -> list[CatalogRow]:
    """Parse one rendered catalog page into rows. Pure function — no IO.

    Skips rows that look like headers, navigation, or are missing the
    detail-page link / a non-empty title. The ``page`` and ``scraped_at``
    arguments are recorded into ``raw_metadata_json`` for forensics.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[CatalogRow] = []
    seen_co_obra: set[str] = set()

    for tr in soup.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        tds = tr.find_all("td", recursive=False)
        if not 7 <= len(tds) <= 9:
            continue
        # Title cell (index 2 in the observed 8-col schema).
        title_td = tds[2]
        if not isinstance(title_td, Tag):
            continue
        anchor = title_td.find("a", href=True)
        if not isinstance(anchor, Tag):
            continue
        title = _normalize_text(anchor.get_text())
        if not title:
            LOG.warning(
                json.dumps(
                    {
                        "ts": scraped_at,
                        "level": "WARNING",
                        "stage": "scrape",
                        "doc_id": None,
                        "action": "skip",
                        "status": "partial",
                        "extra": {"reason": "empty_title", "page": page},
                    }
                )
            )
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        co_obra = _extract_co_obra(href, BASE_URL)
        if co_obra is None or co_obra in seen_co_obra:
            continue
        seen_co_obra.add(co_obra)

        author = _normalize_text(tds[3].get_text()) if len(tds) > 3 else None
        fonte = _normalize_text(tds[4].get_text()) if len(tds) > 4 else None
        formato = _normalize_text(tds[5].get_text()) if len(tds) > 5 else None
        tamanho_text = _normalize_text(tds[6].get_text()) if len(tds) > 6 else None
        acessos_text = _normalize_text(tds[7].get_text()) if len(tds) > 7 else None
        row_num = _normalize_text(tds[0].get_text()).rstrip(" .") if tds else ""

        raw: dict[str, Any] = {
            "row_num": row_num,
            "co_obra": co_obra,
            "title": title,
            "author": author,
            "fonte": fonte,
            "formato": formato,
            "tamanho_text": tamanho_text,
            "acessos_text": acessos_text,
            "page": page,
        }
        rows.append(
            CatalogRow(
                catalog_url=_canonical_catalog_url(co_obra),
                title=title,
                author=author,
                year=_maybe_year_from_text(title, author or ""),
                raw_metadata_json=json.dumps(raw, ensure_ascii=False, sort_keys=True),
                scraped_at=scraped_at,
            )
        )
    return rows


def is_allowed_by_robots(robots_text: str, target_url: str, user_agent: str) -> bool:
    """Parse robots.txt content and return whether ``target_url`` is allowed.

    ``robots_text`` is the literal body. An empty/whitespace string is treated
    as "no rules" → allowed (matches stdlib RobotFileParser behavior).
    """
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_text.splitlines())
    return rp.can_fetch(user_agent, target_url)


def persist_rows(
    conn: sqlite3.Connection,
    rows: Iterable[CatalogRow],
) -> tuple[int, int]:
    """``INSERT OR IGNORE`` rows in one transaction. Returns (inserted, skipped).

    Uses the connection as a context manager so commit/rollback is automatic
    and we don't fight sqlite3's implicit transaction handling.
    """
    inserted = 0
    skipped = 0
    with conn:
        cur = conn.cursor()
        for r in rows:
            cur.execute(
                "INSERT OR IGNORE INTO works "
                "(catalog_url, title, author, year, raw_metadata_json, scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r.catalog_url, r.title, r.author, r.year, r.raw_metadata_json, r.scraped_at),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


# ---------------------------------------------------------------------------
# Live fetch (Playwright). Untested by the unit suite — the parser, persister,
# rate limiter, and robots logic above are covered by tests against fixtures.
# ---------------------------------------------------------------------------


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_with_playwright(
    page_numbers: Iterable[int],
    *,
    rate_limiter: RateLimiter,
) -> Iterable[tuple[int, str]]:
    """Yield (page_number, html) for each page, using a single browser context.

    Imports Playwright lazily so the parser-only test suite does not require
    Chromium to be installed.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            for n in page_numbers:
                rate_limiter.acquire()
                url = BASE_URL.format(page=n)
                page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_selector(RESULT_LINK_SELECTOR, timeout=PAGE_LOAD_TIMEOUT_MS)
                yield n, page.content()
        finally:
            browser.close()


def fetch_robots_txt_with_playwright(rate_limiter: RateLimiter) -> str:
    """Fetch robots.txt via Playwright (plain HTTP gets blocked by CF)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=USER_AGENT)
            page = ctx.new_page()
            rate_limiter.acquire()
            response = page.goto(ROBOTS_URL, timeout=PAGE_LOAD_TIMEOUT_MS)
            if response is None or response.status >= 400:
                return ""
            body_text = page.evaluate("() => document.body && document.body.innerText || ''")
            # Narrow page.evaluate's `Any` return; defensive against the tag's
            # innerText being unexpectedly null.
            return body_text if isinstance(body_text, str) else ""
        finally:
            browser.close()


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def _log(action: str, status: str, **extra: Any) -> None:
    LOG.info(
        json.dumps(
            {
                "ts": _now_iso_utc(),
                "level": "INFO",
                "stage": "scrape",
                "doc_id": None,
                "action": action,
                "status": status,
                "extra": extra or None,
            },
            ensure_ascii=False,
        )
    )


PageFetcher = Callable[[Iterable[int], RateLimiter], Iterable[tuple[int, str]]]
RobotsFetcher = Callable[[RateLimiter], str]


def _default_page_fetcher(
    page_numbers: Iterable[int], rate_limiter: RateLimiter
) -> Iterable[tuple[int, str]]:
    return fetch_with_playwright(page_numbers, rate_limiter=rate_limiter)


def run_scraper(
    db_path: Path | str,
    *,
    max_pages: int | None = None,
    dry_run: bool = False,
    page_fetcher: PageFetcher = _default_page_fetcher,
    robots_fetcher: RobotsFetcher = fetch_robots_txt_with_playwright,
) -> int:
    """End-to-end driver: robots check, paginated fetch, parse, persist.

    Returns a Unix-style exit code (0 ok, non-zero failure). The fetchers are
    injectable so a smoke test can drive the loop with static fixtures instead
    of launching Chromium.
    """
    rate_limiter = RateLimiter(RATE_LIMIT_PER_SECOND)

    robots_text = robots_fetcher(rate_limiter)
    if not is_allowed_by_robots(robots_text, BASE_URL.format(page=1), USER_AGENT):
        _log("fail", "error", reason="robots_disallow", path=SEARCH_PATH)
        return 1

    conn = None if dry_run else db.connect(db_path)
    total_new = 0
    total_seen = 0
    prev_co_obra: frozenset[str] | None = None
    try:
        page_numbers: Iterable[int] = (
            range(1, max_pages + 1) if max_pages is not None else _from_one()
        )
        for page_num, html in page_fetcher(page_numbers, rate_limiter):
            scraped_at = _now_iso_utc()
            rows = parse_catalog_page(html, page=page_num, scraped_at=scraped_at)
            current_co_obra = frozenset(json.loads(r.raw_metadata_json)["co_obra"] for r in rows)
            if not current_co_obra or current_co_obra == prev_co_obra:
                _log("done", "ok", page=page_num, new=0, reason="empty_or_overflow")
                break
            new = seen = 0
            if conn is not None:
                new, seen = persist_rows(conn, rows)
            else:
                new = len(rows)
            total_new += new
            total_seen += seen
            _log("done", "ok", page=page_num, new=new, seen_existing=seen)
            prev_co_obra = current_co_obra
    finally:
        if conn is not None:
            conn.close()

    _log("summary", "ok", total_new=total_new, total_seen=total_seen)
    return 0


def _from_one() -> Iterable[int]:
    n = 1
    while True:
        yield n
        n += 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pindorama.scrape_catalog")
    parser.add_argument(
        "--db",
        default=None,
        help=("Path to metadata.sqlite. Defaults to pindorama.paths.default().metadata_db."),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Hard cap on pages to fetch. Default: walk until empty/overflow.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + parse without writing to the DB.",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    db_path = Path(args.db) if args.db else paths.default().metadata_db
    if not args.dry_run:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return run_scraper(db_path, max_pages=args.max_pages, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
