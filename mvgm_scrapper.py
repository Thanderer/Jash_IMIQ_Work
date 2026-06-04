import hashlib
import re
import time
import json
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL   = "https://www.mvgm.de"
LIST_URL   = "https://www.mvgm.de/de/events"
HEADERS    = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Referer": "https://www.mvgm.de/",
}
DELAY_SEC  = 1.5
MAX_MONTHS = 12

# MVGM venue coords (lat, lon) — used in hash_id
VENUE_COORDS = {
    "AMO Kulturhaus":       (52.1322, 11.6459),
    "Avnet Arena":          (52.1269, 11.6364),
    "Elbauenpark":          (52.1197, 11.5978),
    "GETEC-Arena":          (52.1269, 11.6364),
    "Hyparschale":          (52.1312, 11.6389),
    "Jahrtausendturm":      (52.1197, 11.5978),
    "Johanniskirche":       (52.1237, 11.6378),
    "Messe Magdeburg":      (52.1312, 11.6389),
    "MDCC-Parkbühne":       (52.1197, 11.5978),
    "Seebühne":             (52.1197, 11.5978),
    "Stadthalle Magdeburg": (52.1312, 11.6389),
    "Albinmüller-Turm":     (52.1197, 11.5978),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace — used for hash inputs."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def make_hash_id(name: str, start_dt: str, venue: str) -> str:
    """
    Stable 12-char hex hash from:
      normalized_name + ISO-date-time + lat,lon
    Falls back to (0.0, 0.0) if venue coords unknown.
    """
    coords    = VENUE_COORDS.get(venue, (0.0, 0.0))
    coord_str = f"{coords[0]:.4f},{coords[1]:.4f}"
    raw       = f"{normalize(name)}|{start_dt}|{coord_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def make_series_id(name: str, venue: str) -> str:
    """
    Groups continuous / repeated performances of the SAME show
    at the SAME venue. Keyed on: normalized_name + venue (no date).
    """
    raw = f"{normalize(name)}|{normalize(venue)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def get_soup(url: str, session: requests.Session) -> BeautifulSoup:
    resp = session.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def parse_iso_datetime(date_str: str) -> str:
    date_str = (date_str or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).isoformat()
        except ValueError:
            pass
    return date_str


def parse_time_range(time_text: str):
    """
    '1000 - 1800' → ('10:00', '18:00')
    '1500'        → ('15:00', None)
    """
    time_text = (time_text or "").strip()
    m = re.findall(r"(\d{3,4})", time_text)
    def fmt(t):
        t = t.zfill(4)
        return f"{t[:2]}:{t[2:]}"
    if len(m) >= 2:
        return fmt(m[0]), fmt(m[1])
    elif len(m) == 1:
        return fmt(m[0]), None
    return None, None


# ── Listing parser ────────────────────────────────────────────────────────────

def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    stubs = []
    tiles = soup.select("div.event.event-tiles")
    for tile in tiles:
        title_a = tile.select_one(".textbox .title a[itemprop='url']")
        if not title_a:
            continue
        name_tag   = title_a.select_one("h3[itemprop='name']")
        name       = name_tag.get_text(" ", strip=True) if name_tag else title_a.get_text(strip=True)
        detail_url = urljoin(BASE_URL, title_a.get("href", ""))

        time_tag   = tile.select_one("time[itemprop='startDate']")
        start_raw  = time_tag.get("datetime", "") if time_tag else ""
        time_text  = time_tag.get_text(strip=True) if time_tag else ""

        start_time, end_time = parse_time_range(time_text)

        if start_time and re.match(r"\d{4}-\d{2}-\d{2}$", start_raw):
            start_iso = f"{start_raw}T{start_time}"
        else:
            start_iso = parse_iso_datetime(start_raw)

        loc_div   = tile.select_one(".info.location")
        venue     = loc_div.get_text(strip=True) if loc_div else ""

        cat_div   = tile.select_one(".category")
        category  = cat_div.get_text(strip=True) if cat_div else ""

        img_tag   = tile.select_one("figure img")
        image_url = urljoin(BASE_URL, img_tag.get("src", "")) if img_tag else ""

        stubs.append({
            "name":       name,
            "detail_url": detail_url,
            "start_iso":  start_iso,
            "end_time":   end_time,
            "venue":      venue,
            "category":   category,
            "image_url":  image_url,
        })
    return stubs


# ── Detail page parser ────────────────────────────────────────────────────────

def parse_detail_page(soup: BeautifulSoup, detail_url: str) -> dict:
    detail = {"detail_url": detail_url, "description": "", "end_iso": None, "price": None}

    meta_desc = soup.select_one("meta[name='description']")
    if meta_desc:
        detail["description"] = meta_desc.get("content", "").strip()

    for script in soup.select("script[type='application/ld+json']"):
        try:
            data   = json.loads(script.string or "")
            graphs = data.get("@graph", [data])
            for obj in graphs:
                if obj.get("@type") == "Event":
                    detail["end_iso"]   = obj.get("endDate")
                    detail["price"]     = obj.get("offers", {}).get("price")
                    detail["organizer"] = obj.get("organizer", {}).get("name")
                    if not detail["description"] and obj.get("description"):
                        detail["description"] = obj["description"][:500]
        except (json.JSONDecodeError, AttributeError):
            pass

    return detail


# ── Main scraper ──────────────────────────────────────────────────────────────

def scrape_mvgm(months: int = MAX_MONTHS) -> list[dict]:
    session      = requests.Session()
    all_stubs: list[dict] = []

    now = datetime.now()
    month_params = []
    for i in range(months):
        d = now.replace(day=1) + timedelta(days=32 * i)
        month_params.append(f"{d.year}{d.month:02d}")

    print(f"[mvgm] Scraping {months} months: {month_params[0]} → {month_params[-1]}")

    for month in month_params:
        url = f"{LIST_URL}?month={month}"
        print(f"  Listing: {url}")
        try:
            soup  = get_soup(url, session)
            stubs = parse_listing_page(soup)
            print(f"    → {len(stubs)} events found")
            all_stubs.extend(stubs)
        except requests.RequestException as e:
            print(f"    ✗ {e}")
        time.sleep(DELAY_SEC)

    # Deduplicate by detail_url
    seen_urls: set[str] = set()
    unique_stubs: list[dict] = []
    for s in all_stubs:
        if s["detail_url"] not in seen_urls:
            seen_urls.add(s["detail_url"])
            unique_stubs.append(s)

    print(f"[mvgm] {len(all_stubs)} total → {len(unique_stubs)} unique after dedup")

    # ── Enrich with detail pages ──────────────────────────────────────────────
    events: list[dict] = []
    for i, stub in enumerate(unique_stubs, 1):
        print(f"  Detail {i}/{len(unique_stubs)}: {stub['name'][:60]}")
        try:
            dsoup  = get_soup(stub["detail_url"], session)
            detail = parse_detail_page(dsoup, stub["detail_url"])
        except requests.RequestException as e:
            print(f"    ✗ {e}")
            detail = {}
        time.sleep(DELAY_SEC)

        event = {**stub, **detail}

        event["hash_id"]   = make_hash_id(event["name"], event["start_iso"], event["venue"])
        event["series_id"] = make_series_id(event["name"], event["venue"])
        event["source"]    = "mvgm.de"
        events.append(event)

    # ── Mark series membership ────────────────────────────────────────────────
    from collections import Counter
    series_counts = Counter(e["series_id"] for e in events)
    for e in events:
        e["is_series"]   = series_counts[e["series_id"]] > 1
        e["series_size"] = series_counts[e["series_id"]]

    print(f"[mvgm] Done. {len(events)} events scraped.")
    return events


# ── Output ────────────────────────────────────────────────────────────────────

def save_jsonl(events: list[dict], path: str = "mvgm_events.jsonl") -> None:
    """Write one JSON object per line (JSONL / ndjson format)."""
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"[mvgm] Saved {len(events)} records → {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape MVGM Magdeburg events")
    parser.add_argument("--months",  type=int, default=12,
                        help="Number of months ahead to scrape (default: 12)")
    parser.add_argument("--out",     default="mvgm_events.jsonl",
                        help="Output JSONL file path (default: mvgm_events.jsonl)")
    args = parser.parse_args()

    events = scrape_mvgm(months=args.months)
    save_jsonl(events, args.out)