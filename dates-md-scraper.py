import json
import logging
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL        = "https://www.dates-md.de"
LISTING_URL     = f"{BASE_URL}/search/event/veranstaltungen-magdeburg/"
OUTPUT_PATH     = Path("data/raw/dates_md.jsonl")
REQUEST_DELAY   = 1.5        # seconds between requests
MAX_PAGES       = 200        # safety ceiling
MAX_DETAIL_ERR  = 10         # abort after N consecutive detail fetch errors

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": BASE_URL,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── HTTP helper ───────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)


def get(url: str, retries: int = 3, backoff: float = 3.0) -> Optional[BeautifulSoup]:
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 429:
                wait = backoff * attempt
                log.warning("429 Too Many Requests — waiting %.0fs (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as exc:
            log.warning("Request error [%s] attempt %d: %s", url, attempt, exc)
            time.sleep(backoff)
    log.error("Failed to fetch %s after %d attempts", url, retries)
    return None


# ── Listing page parser ───────────────────────────────────────────────────────
def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    """
    Returns list of stub dicts extracted from inline ld+json blocks on the
    listing page. Each listing card has exactly one ld+json block with:
      @type, name, url, startDate, endDate, description, image, location, eventStatus
    """
    stubs = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            # listing blocks start directly with { "image": ...
            data = json.loads(raw)
            if data.get("@type") in ("https://schema.org/Event", "Event"):
                stubs.append(data)
        except (json.JSONDecodeError, AttributeError):
            continue
    return stubs


def has_next_page(soup: BeautifulSoup) -> bool:
    """True if a 'Nächste' pagination link exists and is not hidden."""
    nxt = soup.select_one("a.next[rel='next']")
    if nxt and "hidden" not in nxt.get("class", []):
        return True
    # fallback: check static paginator
    nxt2 = soup.select_one("div.paginatorstatic a.next")
    return nxt2 is not None


# ── Detail page parser ────────────────────────────────────────────────────────
def parse_detail_page(soup: BeautifulSoup, listing_url: str) -> dict:
    """
    Extract the rich ld+json block from the event detail page, plus all
    future occurrence datetimes listed in <span class="eventoccs">.
    """
    result = {}

    # 1. Rich ld+json block (contains eventSchedule, priceRange, geo, keywords)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = json.loads(raw)
            # detail block has eventSchedule or keywords fields
            if "eventSchedule" in data or "keywords" in data or "priceRange" in data:
                result = data
                break
        except (json.JSONDecodeError, AttributeError):
            continue

    # 2. All occurrence datetimes from <time itemprop="startDate">
    occurrences = []
    for time_tag in soup.select("span.eventoccs span.datetime time[itemprop='startDate']"):
        dt = time_tag.get("datetime")
        if dt:
            occurrences.append(dt)
    result["_all_occurrences"] = occurrences

    # 3. Category / tags from <span class="cats"> on detail page
    cats_tag = soup.select_one("p.cats")
    if cats_tag:
        result["_category"] = cats_tag.get_text(strip=True)

    # 4. Website URL from venue info
    venue_link = soup.select_one("div.eventtags label.url + a")
    if not venue_link:
        venue_link = soup.select_one("div.eventinfos a[href^='http'][rel='noopener']")
    if venue_link:
        result["_venue_website"] = venue_link.get("href", "")

    # 5. Canonical URL (use listing URL as fallback)
    canonical = soup.select_one("link[rel='canonical']")
    result["_canonical_url"] = canonical["href"] if canonical else listing_url

    return result


# ── Record builder ────────────────────────────────────────────────────────────
def build_record(listing_stub: dict, detail: dict, page_url: str) -> dict:
    """Merge listing stub + detail into a clean, flat record."""

    def _loc(d: dict) -> dict:
        return d.get("location", {}) or {}

    def _geo(d: dict) -> dict:
        return _loc(d).get("geo", {}) or {}

    # prefer detail data, fall back to listing stub
    src = detail if detail else listing_stub
    loc = _loc(src) if _loc(src) else _loc(listing_stub)
    geo = _geo(src) if _geo(src) else _geo(listing_stub)

    record = {
        # Identity
        "source":           "dates_md",
        "source_url":       detail.get("_canonical_url") or listing_stub.get("url", ""),
        "scraped_at":       datetime.now(timezone.utc).isoformat(),

        # Core fields
        "name":             src.get("name") or listing_stub.get("name", ""),
        "description":      src.get("description") or listing_stub.get("description", ""),
        "start_date":       src.get("startDate") or listing_stub.get("startDate", ""),
        "end_date":         src.get("endDate") or listing_stub.get("endDate", ""),
        "event_status":     src.get("eventStatus", "https://schema.org/EventScheduled"),
        "keywords":         src.get("keywords", ""),
        "category":         detail.get("_category", ""),
        "image_url":        src.get("image") or listing_stub.get("image", ""),

        # Location
        "venue_name":       loc.get("name", ""),
        "venue_address":    loc.get("address", ""),
        "venue_url":        loc.get("url", ""),
        "venue_website":    detail.get("_venue_website", ""),
        "venue_phone":      loc.get("telephone", ""),
        "venue_same_as":    loc.get("sameAs", ""),
        "geo_lat":          geo.get("latitude", ""),
        "geo_lon":          geo.get("longitude", ""),

        # Pricing
        "price_range":      loc.get("priceRange") or src.get("priceRange", ""),

        # Recurrence
        "is_recurring":     "eventSchedule" in src,
        "repeat_frequency": (src.get("eventSchedule") or {}).get("repeatFrequency", ""),
        "all_occurrences":  detail.get("_all_occurrences", []),
    }
    return record


# ── Main scraper ──────────────────────────────────────────────────────────────
def scrape():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    seen_urls: set[str] = set()
    total_written = 0
    consecutive_detail_errors = 0

    with OUTPUT_PATH.open("w", encoding="utf-8") as fout:
        for page_num in range(1, MAX_PAGES + 1):
            page_url = f"{LISTING_URL}?page={page_num}"
            log.info("Listing page %d → %s", page_num, page_url)

            soup = get(page_url)
            if soup is None:
                log.error("Could not fetch listing page %d — stopping.", page_num)
                break

            stubs = parse_listing_page(soup)
            if not stubs:
                log.info("No events found on page %d — end of listing.", page_num)
                break

            log.info("  Found %d event stubs on page %d", len(stubs), page_num)

            for stub in stubs:
                event_url = stub.get("url", "")
                if not event_url:
                    continue

                # Normalise URL
                if event_url.startswith("http://"):
                    event_url = "https://" + event_url[7:]
                if not event_url.startswith("http"):
                    event_url = BASE_URL + event_url

                # Use the occdtstart variant from listing if present (specific occurrence)
                # canonical slug is without occdtstart for detail fetch
                slug_url = re.sub(r"\?occdtstart=.*$", "", event_url)

                if slug_url in seen_urls:
                    log.debug("  Skipping duplicate: %s", slug_url)
                    continue
                seen_urls.add(slug_url)

                time.sleep(REQUEST_DELAY)
                detail_soup = get(slug_url)

                if detail_soup is None:
                    consecutive_detail_errors += 1
                    log.warning("  Detail fetch failed (%d consecutive): %s",
                                consecutive_detail_errors, slug_url)
                    if consecutive_detail_errors >= MAX_DETAIL_ERR:
                        log.error("Too many consecutive detail errors — aborting.")
                        return
                    detail = {}
                else:
                    consecutive_detail_errors = 0
                    detail = parse_detail_page(detail_soup, slug_url)

                record = build_record(stub, detail, page_url)
                fout.write(json.dumps(record, ensure_ascii=False) + "\\n")
                total_written += 1
                log.info("  [%d] Saved: %s", total_written, record["name"])

            if not has_next_page(soup):
                log.info("No next page link found after page %d — done.", page_num)
                break

            time.sleep(REQUEST_DELAY)

    log.info("Scraping complete. %d records written to %s", total_written, OUTPUT_PATH)


if __name__ == "__main__":
    scrape()