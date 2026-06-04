import re
import json
import time
import logging
import hashlib
import os
from datetime import datetime, timezone
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

# ── Config ─────────────────────────────────────────────────────────────────
BASE_LISTING_URL = "https://veranstaltungen.magdeburg-tourist.de/magdeburg"
OUTPUT_JSONL     = "data/raw/magdeburg_tourist.jsonl"
IMAGES_DIR       = "data/images/magdeburg_tourist"
PAGE_SIZE        = 20
MAX_PAGES        = 50
DELAY_SECONDS    = 1.5
RETRY_ATTEMPTS   = 4
RETRY_BACKOFF    = [3, 6, 12, 25]
REQUEST_TIMEOUT  = 20

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer":         "https://veranstaltungen.magdeburg-tourist.de/magdeburg",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

session = requests.Session()
session.headers.update(HEADERS)


# ── HTTP with retry ──────────────────────────────────────────────────────────
def fetch(url: str) -> str | None:
    for attempt in range(RETRY_ATTEMPTS):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            elif r.status_code in (503, 429, 502, 504):
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                log.warning(f"HTTP {r.status_code} — retrying in {wait}s (attempt {attempt+1}/{RETRY_ATTEMPTS}): {url}")
                time.sleep(wait)
            else:
                log.warning(f"HTTP {r.status_code} — skipping: {url}")
                return None
        except requests.RequestException as e:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            log.warning(f"Request error ({e}) — retrying in {wait}s")
            time.sleep(wait)
    log.error(f"All {RETRY_ATTEMPTS} attempts failed for {url}")
    return None


# ── Image downloader ─────────────────────────────────────────────────────────
def download_image(url: str, images_dir: str) -> str | None:
    if not url:
        return None
    ext = os.path.splitext(url.split("?")[0])[-1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    slug = hashlib.md5(url.encode()).hexdigest()[:16]
    local_path = os.path.join(images_dir, f"{slug}{ext}")
    if os.path.exists(local_path):
        return local_path
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        r.raise_for_status()
        os.makedirs(images_dir, exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return local_path
    except Exception as e:
        log.warning(f"Image download failed ({url}): {e}")
        return None


# ── Pagination URL builder ───────────────────────────────────────────────────
# Correct format discovered from HTML source:
#   https://veranstaltungen.magdeburg-tourist.de/magdeburg?os=20&sendfoo
# Note: no trailing slash, "sendfoo" as a bare flag (not send=foo)
def listing_url(offset: int) -> str:
    if offset == 0:
        return BASE_LISTING_URL
    return f"{BASE_LISTING_URL}?os={offset}&sendfoo"


def has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.find_all("a", href=re.compile(r"os=\d+")))


# ── Listing page parser ──────────────────────────────────────────────────────
def parse_listing_page(html: str) -> tuple[list[dict], BeautifulSoup]:
    soup = BeautifulSoup(html, "html.parser")
    events = []
    seen_urls: set[str] = set()

    for row in soup.find_all("div", class_="row"):
        img_tag = row.find("img")
        if not img_tag:
            continue
        src = img_tag.get("src", "")
        if not any(d in src for d in ["rce-event", "magdeburg-tourist.de", "gfs-magdeburg", "eventim.de"]):
            continue

        # Find detail URL (has long hash suffix)
        detail_url = ""
        for a in row.find_all("a", href=re.compile(r"/magdeburg/")):
            href = a.get("href", "")
            if re.search(r"-[a-f0-9]{20,}", href):
                detail_url = href
                break
        if not detail_url or detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        # Ensure https + absolute
        if detail_url.startswith("http://"):
            detail_url = "https://" + detail_url[7:]
        elif detail_url.startswith("/"):
            detail_url = "https://veranstaltungen.magdeburg-tourist.de" + detail_url

        # Title
        h3 = row.find("h3")
        title = h3.get_text(strip=True) if h3 else ""

        # Category
        category = ""
        col = row.find("div", class_=lambda c: c and "col-md-8" in c)
        if col:
            spans = col.find_all("span", class_="text-color-1")
            if spans:
                category = spans[0].get_text(strip=True)

        # Thumbnail — handle proxy (?src=...) and direct URLs
        thumbnail_url = ""
        raw_src = img_tag.get("src", "")
        proxy_match = re.search(r"[?&]src=([^&]+)", raw_src)
        if proxy_match:
            thumbnail_url = unquote(proxy_match.group(1))
        elif raw_src.startswith("http"):
            thumbnail_url = raw_src
        elif raw_src.startswith("/"):
            thumbnail_url = "https://veranstaltungen.magdeburg-tourist.de" + raw_src

        events.append({
            "source":        "magdeburg-tourist",
            "name":          title,
            "category":      category,
            "source_urls":   [detail_url],
            "thumbnail_url": thumbnail_url,
        })

    return events, soup


# ── Detail page parser ───────────────────────────────────────────────────────
def parse_detail_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    details: dict = {}

    # Description
    desc_div = soup.find("div", class_="description")
    if desc_div:
        details["description"] = desc_div.get_text(separator="\n", strip=True)

    # Coordinates
    lat_input = soup.find("input", id="geob")
    lon_input = soup.find("input", id="geol")
    if lat_input and lon_input and lat_input.get("value") and lon_input.get("value"):
        try:
            details["location_coords"] = [
                float(lon_input["value"]),
                float(lat_input["value"]),
            ]
        except ValueError:
            pass

    # Venue name + address
    ort_header = soup.find(string=lambda t: t and "Ort" in t)
    if ort_header:
        address_tag = ort_header.find_next("address")
        if address_tag:
            venue_name = address_tag.find("strong")
            if venue_name:
                details["location_name_raw"] = venue_name.get_text(strip=True)
            address_p = address_tag.find("p")
            if address_p:
                details["address"] = address_p.get_text(separator=" ", strip=True)

    # Dates & times
    for box in soup.find_all("div", class_="box-content"):
        text = box.get_text(" ", strip=True)

        if "start_date" not in details:
            d = re.search(
                r"(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),?\s+(\d{2}\.\d{2}\.\d{2,4})",
                text,
            )
            if d:
                raw = d.group(1)
                if len(raw) == 8:
                    raw = raw[:6] + "20" + raw[6:]
                details["start_date"] = raw

        if "start_time" not in details:
            t = re.search(
                r"(?:ab|von)\s+(\d{2}:\d{2})(?:\s+bis\s+(\d{2}:\d{2}))?\s+Uhr",
                text,
            )
            if t:
                details["start_time"] = t.group(1)
                if t.group(2):
                    details["end_time"] = t.group(2)

    # Recurring dates
    for box in soup.find_all("div", class_="box-content"):
        text = box.get_text(" ", strip=True)
        dates = re.findall(
            r"\d{2}\.\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s*\d{4}",
            text,
        )
        if len(dates) > 1:
            details["recurring_dates"] = dates
            break

    return details


# ── Writer ───────────────────────────────────────────────────────────────────
def append_to_jsonl(record: dict, filename: str = OUTPUT_JSONL) -> None:
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Orchestrator ─────────────────────────────────────────────────────────────
def scrape():
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    open(OUTPUT_JSONL, "w").close()

    # Stage 1: collect all listing stubs
    offset = 0
    page_num = 0
    listing_events: dict[str, dict] = {}

    while page_num < MAX_PAGES:
        url = listing_url(offset)
        log.info(f"Listing page {page_num + 1} (os={offset}) → {url}")
        html = fetch(url)
        if not html:
            log.warning("Failed to fetch listing page — stopping pagination.")
            break

        events, soup = parse_listing_page(html)
        if not events:
            log.info("No events on this page — stopping pagination.")
            break

        new_count = 0
        for e in events:
            key = e["source_urls"][0]
            if key not in listing_events:
                listing_events[key] = e
                new_count += 1

        log.info(f"  {len(events)} events on page ({new_count} new) | {len(listing_events)} total")

        if not has_next_page(soup):
            log.info("No next page link — pagination complete.")
            break

        offset += PAGE_SIZE
        page_num += 1
        time.sleep(DELAY_SECONDS)

    total = len(listing_events)
    log.info(f"Listing complete: {total} unique events. Starting detail extraction...")

    # Stage 2: enrich + download images
    for i, (url_key, event) in enumerate(listing_events.items(), 1):
        log.info(f"[{i}/{total}] {url_key[:90]}")
        detail_html = fetch(url_key)
        time.sleep(DELAY_SECONDS)

        detail = parse_detail_page(detail_html) if detail_html else {}
        merged = {**event, **detail}

        thumb = merged.get("thumbnail_url", "")
        merged["image_local"] = download_image(thumb, IMAGES_DIR) if thumb else None
        merged["scraped_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        append_to_jsonl(merged)

    log.info(f"Done. {total} events saved to {OUTPUT_JSONL}")


if __name__ == "__main__":
    scrape()