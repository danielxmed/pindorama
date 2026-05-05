"""Tests for src/pindorama/scrape_catalog.py.

Network is never touched — every test that exercises the parser uses a static
HTML fixture under ``tests/fixtures/dominiopublico/``. Live Playwright code
paths (``fetch_with_playwright`` / ``fetch_robots_txt_with_playwright``) are
deliberately not unit-tested here; they're integration-tested in production
via ``slurm/scrape_or_download.sh``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from pindorama import db
from pindorama.scrape_catalog import (
    BASE_URL,
    USER_AGENT,
    CatalogRow,
    RateLimiter,
    _canonical_catalog_url,
    _extract_co_obra,
    _maybe_year_from_text,
    _normalize_text,
    is_allowed_by_robots,
    parse_catalog_page,
    persist_rows,
    run_scraper,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dominiopublico"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_text_collapses_whitespace_and_nfc() -> None:
    # The catalog HTML uses precomposed accents; ensure decomposed (NFD)
    # input some sources emit is normalized to NFC.
    decomposed = "Salomão"  # 'a' + combining tilde
    assert _normalize_text(f"  {decomposed}\n  Rovedo  ") == "Salomão Rovedo"


def test_extract_co_obra_returns_digit_string_or_none() -> None:
    assert _extract_co_obra("DetalheObraForm.do?co_obra=7632", BASE_URL) == "7632"
    # select_action ordering should not matter
    assert _extract_co_obra("DetalheObraForm.do?select_action=&co_obra=7425", BASE_URL) == "7425"
    assert _extract_co_obra("DetalheObraForm.do", BASE_URL) is None
    assert _extract_co_obra("DetalheObraForm.do?co_obra=", BASE_URL) is None
    assert _extract_co_obra("DetalheObraForm.do?co_obra=abc", BASE_URL) is None


def test_canonical_catalog_url_is_stable() -> None:
    assert (
        _canonical_catalog_url("7632")
        == "https://dominiopublico.gov.br/pesquisa/DetalheObraForm.do?co_obra=7632"
    )


def test_year_parser_picks_up_4digit_in_range() -> None:
    assert _maybe_year_from_text("Memórias Póstumas (1881)") == 1881
    assert _maybe_year_from_text("Volume 2", "Edição 1900") == 1900


def test_year_parser_rejects_out_of_range_and_garbage() -> None:
    assert _maybe_year_from_text("345", "Artur Azevedo") is None
    assert _maybe_year_from_text("Title 99999", "0042") is None
    assert _maybe_year_from_text("") is None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_populated_page_returns_expected_rows() -> None:
    rows = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    assert len(rows) == 8
    titles = [r.title for r in rows]
    assert "14 de Julho na roça" in titles
    assert "345" in titles
    assert 'A "Não-me-toques"!' in titles
    assert "A Cidade e as Serras" in titles
    # Catalog URL is canonical and keyed on co_obra.
    assert (
        rows[0].catalog_url
        == "https://dominiopublico.gov.br/pesquisa/DetalheObraForm.do?co_obra=7632"
    )
    # Author preserved with diacritics, NFC.
    assert rows[0].author == "Raul Pompéia"


def test_parse_records_raw_metadata_json_with_size_and_source() -> None:
    rows = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    raw = json.loads(rows[0].raw_metadata_json)
    assert raw["co_obra"] == "7632"
    assert raw["fonte"] == "[bi] Biblioteca Virtual de Literatura"
    assert raw["tamanho_text"] == "873,48 KB"
    assert raw["acessos_text"] == "36.823"
    assert raw["page"] == 1


def test_parse_year_is_none_for_literature_rows() -> None:
    # The catalog list never embeds publication year for literary works.
    rows = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    assert all(r.year is None for r in rows), [r for r in rows if r.year is not None]


def test_parse_empty_fixture_returns_empty_list() -> None:
    minimal = "<!DOCTYPE html><html><body><table><tr><th>x</th></tr></table></body></html>"
    assert parse_catalog_page(minimal, page=99, scraped_at="2026-05-04T22:50:00Z") == []


def test_parse_malformed_skips_bad_rows_keeps_good_ones() -> None:
    rows = parse_catalog_page(
        _read("page_malformed.html"), page=1, scraped_at="2026-05-04T22:50:00Z"
    )
    titles = sorted(r.title for r in rows)
    assert titles == ["14 de Julho na roça", 'A "Não-me-toques"!']


def test_parse_dedups_within_a_single_page() -> None:
    # If the page somehow lists the same co_obra twice, the parser keeps one.
    rows = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    co_obras = [json.loads(r.raw_metadata_json)["co_obra"] for r in rows]
    assert len(co_obras) == len(set(co_obras))


def test_overflow_fixture_yields_same_co_obra_set_as_page1() -> None:
    page1 = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    overflow = parse_catalog_page(
        _read("page_overflow.html"), page=43, scraped_at="2026-05-04T22:50:01Z"
    )
    p1 = {json.loads(r.raw_metadata_json)["co_obra"] for r in page1}
    po = {json.loads(r.raw_metadata_json)["co_obra"] for r in overflow}
    assert p1 == po


# ---------------------------------------------------------------------------
# Persistence (idempotency)
# ---------------------------------------------------------------------------


def test_persist_inserts_then_dedups_on_replay(tmp_path: Path) -> None:
    rows = parse_catalog_page(_read("page_001.html"), page=1, scraped_at="2026-05-04T22:50:00Z")
    conn = db.connect(tmp_path / "metadata.sqlite")
    try:
        first_new, first_skipped = persist_rows(conn, rows)
        assert first_new == 8 and first_skipped == 0

        second_new, second_skipped = persist_rows(conn, rows)
        assert second_new == 0 and second_skipped == 8

        n_works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        assert n_works == 8

        # Defaults from schema applied (Stage 2 hasn't run yet).
        statuses = {row[0] for row in conn.execute("SELECT download_status FROM works").fetchall()}
        assert statuses == {"pending"}
    finally:
        conn.close()


def test_persist_writes_expected_columns(tmp_path: Path) -> None:
    rows = [
        CatalogRow(
            catalog_url="https://example/1",
            title="Whatever",
            author="Some Author",
            year=None,
            raw_metadata_json='{"k":"v"}',
            scraped_at="2026-05-04T22:50:00Z",
        )
    ]
    conn = db.connect(tmp_path / "metadata.sqlite")
    try:
        persist_rows(conn, rows)
        row = conn.execute(
            "SELECT catalog_url, title, author, year, raw_metadata_json, scraped_at, "
            "pdf_url, file_path, file_sha256 FROM works"
        ).fetchone()
        assert row[:6] == (
            "https://example/1",
            "Whatever",
            "Some Author",
            None,
            '{"k":"v"}',
            "2026-05-04T22:50:00Z",
        )
        # Stage 1 leaves Stage-2+ columns empty.
        assert row[6] is None and row[7] is None and row[8] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rate limiter (mock clock + sleep)
# ---------------------------------------------------------------------------


class FakeClock:
    """Deterministic monotonic clock paired with a sleep that advances it."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        # Negative or zero sleeps are no-ops (mirroring time.sleep semantics).
        if seconds > 0:
            self.sleeps.append(seconds)
            self.now += seconds


def test_rate_limiter_first_call_does_not_sleep() -> None:
    clock = FakeClock()
    rl = RateLimiter(2.0, now_fn=clock.time, sleep_fn=clock.sleep)
    rl.acquire()
    assert clock.sleeps == []


def test_rate_limiter_enforces_minimum_gap() -> None:
    clock = FakeClock()
    rl = RateLimiter(2.0, now_fn=clock.time, sleep_fn=clock.sleep)
    rl.acquire()  # at t=0
    rl.acquire()  # immediate retry — must sleep ~0.5s
    rl.acquire()  # immediate retry — must sleep another ~0.5s
    assert len(clock.sleeps) == 2
    assert all(0.49 <= s <= 0.51 for s in clock.sleeps), clock.sleeps


def test_rate_limiter_does_not_sleep_when_caller_is_already_slow() -> None:
    clock = FakeClock()
    rl = RateLimiter(2.0, now_fn=clock.time, sleep_fn=clock.sleep)
    rl.acquire()  # at t=0
    clock.now = 5.0  # caller did its own work for 5s
    rl.acquire()
    assert clock.sleeps == []


def test_rate_limiter_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0.0)
    with pytest.raises(ValueError):
        RateLimiter(-1.0)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def test_robots_disallow_blocks_search_path() -> None:
    robots = (FIXTURES / "robots_disallow.txt").read_text(encoding="utf-8")
    assert is_allowed_by_robots(robots, BASE_URL.format(page=1), USER_AGENT) is False


def test_robots_empty_content_treated_as_allow_all() -> None:
    assert is_allowed_by_robots("", BASE_URL.format(page=1), USER_AGENT) is True


def test_robots_explicit_allow_overrides_default() -> None:
    text = "User-agent: *\nAllow: /pesquisa/\n"
    assert is_allowed_by_robots(text, BASE_URL.format(page=1), USER_AGENT) is True


# ---------------------------------------------------------------------------
# run_scraper smoke (driver glue, with injected fakes)
# ---------------------------------------------------------------------------


def test_run_scraper_walks_pages_and_breaks_on_overflow(tmp_path: Path) -> None:
    p1_html = _read("page_001.html")
    p_overflow_html = _read("page_overflow.html")

    def fake_pages(
        page_numbers: Iterable[int],
        rate_limiter: RateLimiter,
    ) -> Iterable[tuple[int, str]]:
        del rate_limiter
        for n in page_numbers:
            yield n, (p1_html if n == 1 else p_overflow_html)

    def fake_robots(rate_limiter: RateLimiter) -> str:
        del rate_limiter
        return ""

    db_path = tmp_path / "metadata.sqlite"
    rc = run_scraper(
        db_path,
        max_pages=5,
        page_fetcher=fake_pages,
        robots_fetcher=fake_robots,
    )
    assert rc == 0

    conn = db.connect(db_path)
    try:
        n_works = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    finally:
        conn.close()
    assert n_works == 8  # 8 from page_001; page_2 (overflow) dedups to zero new


def test_run_scraper_aborts_on_robots_disallow(tmp_path: Path) -> None:
    fetched_pages: list[int] = []

    def fake_pages(
        page_numbers: Iterable[int], rate_limiter: RateLimiter
    ) -> Iterable[tuple[int, str]]:
        del rate_limiter
        for n in page_numbers:
            fetched_pages.append(n)
            yield n, ""

    def fake_robots(rate_limiter: RateLimiter) -> str:
        del rate_limiter
        return "User-agent: *\nDisallow: /pesquisa/\n"

    rc = run_scraper(
        tmp_path / "metadata.sqlite",
        max_pages=1,
        page_fetcher=fake_pages,
        robots_fetcher=fake_robots,
    )
    assert rc == 1
    assert fetched_pages == []  # never reached the fetch loop
