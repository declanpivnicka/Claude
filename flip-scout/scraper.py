"""Crawl Obenauf auction catalogs with a headless browser.

The bid site's markup isn't something we control (and it changes), so this
module stays deliberately generic: it renders each page, keeps the visible text
and the links, and leaves understanding the lots to the screening model.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

from playwright.sync_api import Browser, Page, TimeoutError as PWTimeout, sync_playwright

log = logging.getLogger(__name__)

MAX_PAGE_TEXT = 60_000
PAGINATION_TEXT = re.compile(r"^\s*(\d{1,3}|next|next page|›|»|>|>>)\s*$", re.I)
CLOSED_TEXT = re.compile(r"^\s*(closed|ended|completed)\b", re.I)


@dataclass
class Link:
    text: str
    url: str


@dataclass
class PageSnapshot:
    url: str
    title: str
    text: str
    links: list[Link] = field(default_factory=list)


@dataclass
class CatalogPage:
    auction_url: str
    auction_title: str
    snapshot: PageSnapshot
    item_links: list[Link]


class Crawler:
    def __init__(self, cfg: dict, dump_dir: Path | None = None):
        self.cfg = cfg
        self.auction_res = [re.compile(p, re.I) for p in cfg["auction_url_patterns"]]
        self.item_res = [re.compile(p, re.I) for p in cfg["item_url_patterns"]]
        self.dump_dir = dump_dir
        self._pw = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._dumps = 0

    def __enter__(self) -> "Crawler":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None
        )
        ctx = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 2000},
        )
        self._page = ctx.new_page()
        return self

    def __exit__(self, *exc) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    # -- low level -------------------------------------------------------------

    def snapshot(self, url: str) -> PageSnapshot | None:
        page = self._page
        assert page is not None
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass  # long-polling bid sites never go idle; what we have is fine
            # Lazy-loaded catalogs: scroll a few screens so every lot renders.
            for _ in range(6):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(400)
            text = page.inner_text("body")
            raw_links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(a => [a.innerText || a.title || '', a.getAttribute('href')])",
            )
            title = page.title()
        except Exception as e:  # network errors, timeouts, crashed tabs
            log.warning("failed to load %s: %s", url, e)
            return None

        links = []
        for t, href in raw_links:
            if not href or href.startswith(("javascript:", "mailto:", "tel:")):
                continue
            links.append(Link(" ".join(t.split())[:200], urldefrag(urljoin(page.url, href))[0]))
        snap = PageSnapshot(page.url, title, text[:MAX_PAGE_TEXT], links)
        self._dump(snap)
        return snap

    def _dump(self, snap: PageSnapshot) -> None:
        if not self.dump_dir:
            return
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self._dumps += 1
        body = [f"URL: {snap.url}", f"TITLE: {snap.title}", "", snap.text, "", "LINKS:"]
        body += [f"{l.text} -> {l.url}" for l in snap.links]
        (self.dump_dir / f"{self._dumps:03d}.txt").write_text("\n".join(body))

    def _is_auction(self, url: str) -> bool:
        return any(r.search(url) for r in self.auction_res)

    def _is_item(self, url: str) -> bool:
        return any(r.search(url) for r in self.item_res)

    # -- crawl -----------------------------------------------------------------

    def discover_auctions(self) -> list[Link]:
        found: dict[str, Link] = {}
        for start in self.cfg["start_urls"]:
            snap = self.snapshot(start)
            if not snap:
                continue
            if self._is_auction(snap.url):
                found.setdefault(snap.url, Link(snap.title, snap.url))
            for link in snap.links:
                if self._is_auction(link.url) and not CLOSED_TEXT.match(link.text):
                    # Keep the most descriptive anchor text we've seen for the URL.
                    prev = found.get(link.url)
                    if not prev or len(link.text) > len(prev.text):
                        found[link.url] = link
        auctions = list(found.values())[: self.cfg["max_auctions_per_run"]]
        log.info("discovered %d open auction catalogs", len(auctions))
        return auctions

    def crawl_catalog(self, auction: Link) -> list[CatalogPage]:
        pages: list[CatalogPage] = []
        queue = [auction.url]
        visited: set[str] = set()
        title = auction.text
        while queue and len(pages) < self.cfg["max_catalog_pages_per_auction"]:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            snap = self.snapshot(url)
            if not snap:
                continue
            title = title or snap.title
            if CLOSED_TEXT.match(snap.title):
                log.info("skipping closed auction %s", snap.title)
                return []
            items = _dedupe([l for l in snap.links if self._is_item(l.url)])
            pages.append(CatalogPage(auction.url, title or snap.title, snap, items))
            # Follow pagination links that stay inside this catalog.
            for link in snap.links:
                if (
                    link.url not in visited
                    and self._is_auction(link.url)
                    and _same_auction(link.url, auction.url)
                    and (PAGINATION_TEXT.match(link.text) or "pagenumber" in link.url.lower())
                ):
                    queue.append(link.url)
        log.info("%s: %d catalog page(s)", title, len(pages))
        return pages


def _auction_id(url: str) -> str | None:
    q = {k.lower(): v for k, v in parse_qs(urlparse(url).query).items()}
    return q.get("auctionid", [None])[0]


def _same_auction(a: str, b: str) -> bool:
    ida, idb = _auction_id(a), _auction_id(b)
    return ida is None or idb is None or ida == idb


def _dedupe(links: list[Link]) -> list[Link]:
    seen: dict[str, Link] = {}
    for l in links:
        prev = seen.get(l.url)
        if not prev or len(l.text) > len(prev.text):
            seen[l.url] = l
    return list(seen.values())
