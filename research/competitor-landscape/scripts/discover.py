#!/usr/bin/env python3
"""Discover portrait-photography competitors from static Brave Search HTML.

The script performs ordinary HTTP GET requests and never executes page JavaScript.
It writes a query log and a deduplicated candidate-domain CSV. Search results are
time-varying, so the retrieval timestamp and exact query are retained for audit.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.parse import unquote, urlparse

import requests
from lxml import html


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SEARCH_URLS = {
    "yahoo": "https://search.yahoo.com/search",
    "brave": "https://search.brave.com/search",
}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
    "CompetitorLandscapeResearch/1.0"
)

# Broad geographic coverage keeps the result from being a style echo chamber.
# Labels are deliberately plain search strings rather than a proprietary dataset.
MARKETS = [
    "Memphis TN", "Nashville TN", "Knoxville TN", "Chattanooga TN", "Jackson TN",
    "Atlanta GA", "Savannah GA", "Augusta GA", "Columbus GA", "Macon GA",
    "Birmingham AL", "Huntsville AL", "Mobile AL", "Montgomery AL", "Tuscaloosa AL",
    "Little Rock AR", "Fayetteville AR", "Fort Smith AR", "Jonesboro AR", "Conway AR",
    "Jackson MS", "Gulfport MS", "Oxford MS", "Hattiesburg MS", "Tupelo MS",
    "Louisville KY", "Lexington KY", "Bowling Green KY", "Owensboro KY", "Paducah KY",
    "New Orleans LA", "Baton Rouge LA", "Lafayette LA", "Shreveport LA", "Lake Charles LA",
    "Dallas TX", "Fort Worth TX", "Houston TX", "Austin TX", "San Antonio TX",
    "El Paso TX", "Corpus Christi TX", "Lubbock TX", "Amarillo TX", "Waco TX",
    "Oklahoma City OK", "Tulsa OK", "Norman OK", "Edmond OK", "Broken Arrow OK",
    "Orlando FL", "Tampa FL", "Miami FL", "Jacksonville FL", "Fort Lauderdale FL",
    "West Palm Beach FL", "Naples FL", "Sarasota FL", "Tallahassee FL", "Gainesville FL",
    "Charlotte NC", "Raleigh NC", "Durham NC", "Greensboro NC", "Wilmington NC",
    "Charleston SC", "Columbia SC", "Greenville SC", "Myrtle Beach SC", "Hilton Head SC",
    "Richmond VA", "Virginia Beach VA", "Norfolk VA", "Charlottesville VA", "Roanoke VA",
    "Washington DC", "Baltimore MD", "Annapolis MD", "Frederick MD", "Bethesda MD",
    "Philadelphia PA", "Pittsburgh PA", "Harrisburg PA", "Lancaster PA", "Allentown PA",
    "New York NY", "Brooklyn NY", "Long Island NY", "Albany NY", "Buffalo NY",
    "Boston MA", "Worcester MA", "Springfield MA", "Providence RI", "Hartford CT",
    "Portland ME", "Manchester NH", "Burlington VT", "Newark NJ", "Princeton NJ",
    "Cleveland OH", "Columbus OH", "Cincinnati OH", "Dayton OH", "Toledo OH",
    "Detroit MI", "Grand Rapids MI", "Ann Arbor MI", "Lansing MI", "Traverse City MI",
    "Indianapolis IN", "Fort Wayne IN", "South Bend IN", "Evansville IN", "Bloomington IN",
    "Chicago IL", "Naperville IL", "Rockford IL", "Peoria IL", "Springfield IL",
    "Milwaukee WI", "Madison WI", "Green Bay WI", "Eau Claire WI", "Appleton WI",
    "Minneapolis MN", "Saint Paul MN", "Duluth MN", "Rochester MN", "Saint Cloud MN",
    "Des Moines IA", "Cedar Rapids IA", "Iowa City IA", "Davenport IA", "Ames IA",
    "Saint Louis MO", "Kansas City MO", "Springfield MO", "Columbia MO", "Branson MO",
    "Omaha NE", "Lincoln NE", "Wichita KS", "Topeka KS", "Overland Park KS",
    "Denver CO", "Colorado Springs CO", "Boulder CO", "Fort Collins CO", "Aspen CO",
    "Salt Lake City UT", "Provo UT", "Park City UT", "Boise ID", "Coeur d'Alene ID",
    "Phoenix AZ", "Scottsdale AZ", "Tucson AZ", "Flagstaff AZ", "Sedona AZ",
    "Albuquerque NM", "Santa Fe NM", "Las Vegas NV", "Reno NV", "Henderson NV",
    "Los Angeles CA", "San Diego CA", "San Francisco CA", "Sacramento CA", "San Jose CA",
    "Orange County CA", "Santa Barbara CA", "Palm Springs CA", "Fresno CA", "Monterey CA",
    "Portland OR", "Eugene OR", "Bend OR", "Seattle WA", "Tacoma WA",
    "Spokane WA", "Bellevue WA", "Anchorage AK", "Honolulu HI", "Maui HI",
    "Toronto ON", "Vancouver BC", "Calgary AB", "Edmonton AB", "Ottawa ON",
    "Montreal QC", "Halifax NS", "Winnipeg MB", "Victoria BC", "Kelowna BC",
]

CATEGORY_QUERIES = {
    "family": '"family photographer" {market}',
    "senior": '"senior portrait photographer" {market}',
    "children_newborn": '(children OR newborn) "portrait photographer" {market}',
    "fine_art_full_service": '("fine art" OR "full service") "portrait studio" {market}',
}

EXCLUDED_HOST_PARTS = {
    "500px.com", "airbnb.com", "amazon.com", "angi.com", "bark.com", "behance.net",
    "bing.com", "brave.com", "canva.com", "craigslist.org", "etsy.com", "facebook.com",
    "flickr.com", "gigmasters.com", "gigsalad.com", "google.com", "hackerone.com",
    "houzz.com", "instagram.com", "linkedin.com", "mapquest.com", "medium.com",
    "nextdoor.com", "pinterest.com", "reddit.com", "snappr.com", "tiktok.com",
    "theknot.com", "thumbtack.com", "tripadvisor.com", "weddingwire.com", "wikipedia.org",
    "x.com", "yellowpages.com", "yelp.com", "youtube.com", "zola.com",
}


def normalize_host(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if not host or "." not in host:
            return ""
        host.encode("idna")
        return host
    except (ValueError, UnicodeError):
        return ""


def is_excluded(host: str) -> bool:
    return any(host == bad or host.endswith("." + bad) for bad in EXCLUDED_HOST_PARTS)


def clean_text(value: str) -> str:
    value = html_lib.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def yahoo_target(url: str) -> str:
    """Decode Yahoo's ordinary result redirect without requesting the redirect."""
    match = re.search(r"/RU=([^/]+)/RK=", url)
    return unquote(match.group(1)) if match else url


def extract_results(page: bytes, engine: str) -> List[Tuple[str, str, str]]:
    """Return (url, result_title, result_snippet) from static result cards."""
    try:
        doc = html.fromstring(page)
    except (ValueError, html.etree.ParserError):
        return []
    found: List[Tuple[str, str, str]] = []
    if engine == "yahoo":
        cards = doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' algo ')]")
    else:
        cards = doc.xpath(
            "//*[contains(concat(' ', normalize-space(@class), ' '), ' result-wrapper ')]"
        )
    for card in cards:
        url = ""
        for anchor in card.xpath(".//a[@href]"):
            candidate = anchor.get("href", "")
            if engine == "yahoo":
                candidate = yahoo_target(candidate)
            host = normalize_host(candidate)
            if candidate.startswith(("http://", "https://")) and host and not is_excluded(host):
                url = candidate
                break
        if not url:
            continue
        if engine == "yahoo":
            title_nodes = card.xpath(".//h3")
            snippet_nodes = card.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' compText ')]/p"
            )
        else:
            title_nodes = card.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' search-snippet-title ')]"
            )
            snippet_nodes = card.xpath(
                ".//*[contains(concat(' ', normalize-space(@class), ' '), ' generic-snippet ')]"
            )
        title = clean_text(title_nodes[0].text_content()) if title_nodes else ""
        snippet = clean_text(snippet_nodes[0].text_content()) if snippet_nodes else ""
        found.append((url, title, snippet))
    return found


def make_queries(seed: int) -> List[Tuple[str, str, str]]:
    queries = [
        (category, market, template.format(market=market))
        for market in MARKETS
        for category, template in CATEGORY_QUERIES.items()
    ]
    random.Random(seed).shuffle(queries)
    return queries


def fetch_search(
    session: requests.Session, query: str, engine: str, attempts: int = 3
) -> Tuple[int, bytes, str]:
    last_error = ""
    for attempt in range(attempts):
        try:
            params = (
                {"p": query, "n": "10"}
                if engine == "yahoo"
                else {"q": query, "source": "web", "spellcheck": "0"}
            )
            response = session.get(
                SEARCH_URLS[engine],
                params=params,
                timeout=(8, 35),
            )
            expected_marker = b" algo " if engine == "yahoo" else b"result-wrapper"
            if response.status_code == 200 and expected_marker in response.content:
                return response.status_code, response.content, ""
            last_error = f"HTTP {response.status_code}; {len(response.content)} bytes"
            if response.status_code not in (403, 429, 500, 502, 503, 504):
                break
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(3 * (attempt + 1))
    return 0, b"", last_error


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-queries", type=int, default=360)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--delay", type=float, default=0.7, help="Minimum delay between search requests")
    parser.add_argument("--engine", choices=sorted(SEARCH_URLS), default="yahoo")
    args = parser.parse_args()

    queries = make_queries(args.seed)[: args.max_queries]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})

    candidates: Dict[str, Dict[str, object]] = {}
    query_rows: List[Dict[str, str]] = []
    started = time.monotonic()
    for index, (category, market, query) in enumerate(queries, 1):
        request_started = time.monotonic()
        status, page, error = fetch_search(session, query, args.engine)
        results = extract_results(page, args.engine) if page else []
        retrieved = datetime.now(timezone.utc).isoformat()
        query_rows.append(
            {
                "query_number": str(index),
                "source": args.engine,
                "category": category,
                "market": market,
                "query": query,
                "http_status": str(status),
                "result_count": str(len(results)),
                "retrieved_at_utc": retrieved,
                "error": error,
            }
        )
        for rank, (url, result_title, snippet) in enumerate(results, 1):
            host = normalize_host(url)
            if not host or is_excluded(host) or host == "mattkingphotography.com":
                continue
            record = candidates.setdefault(
                host,
                {
                    "domain": host,
                    "discovered_url": url,
                    "first_category": category,
                    "first_market": market,
                    "first_query": query,
                    "first_rank": str(rank),
                    "result_title": result_title,
                    "result_snippet": snippet,
                    "categories": set(),
                    "markets": set(),
                    "queries": set(),
                },
            )
            record["categories"].add(category)  # type: ignore[union-attr]
            record["markets"].add(market)  # type: ignore[union-attr]
            record["queries"].add(query)  # type: ignore[union-attr]
        if index % 10 == 0 or index == len(queries):
            elapsed = time.monotonic() - started
            successes = sum(row["http_status"] == "200" for row in query_rows)
            print(
                f"queries={index}/{len(queries)} successes={successes} "
                f"unique_candidates={len(candidates)} elapsed={elapsed:.1f}s",
                flush=True,
            )
        wait = args.delay - (time.monotonic() - request_started)
        if wait > 0:
            time.sleep(wait + random.random() * 0.25)

    candidate_rows = []
    for host, record in sorted(candidates.items()):
        candidate_rows.append(
            {
                "domain": host,
                "discovered_url": str(record["discovered_url"]),
                "first_category": str(record["first_category"]),
                "first_market": str(record["first_market"]),
                "first_query": str(record["first_query"]),
                "first_rank": str(record["first_rank"]),
                "result_title": str(record["result_title"]),
                "result_snippet": str(record["result_snippet"]),
                "categories": "|".join(sorted(record["categories"])),  # type: ignore[arg-type]
                "markets": "|".join(sorted(record["markets"])),  # type: ignore[arg-type]
                "query_count": str(len(record["queries"])),  # type: ignore[arg-type]
            }
        )

    write_csv(
        DATA_DIR / "discovery_queries.csv",
        [
            "query_number", "source", "category", "market", "query", "http_status",
            "result_count", "retrieved_at_utc", "error",
        ],
        query_rows,
    )
    write_csv(
        DATA_DIR / "candidate_domains.csv",
        [
            "domain", "discovered_url", "first_category", "first_market", "first_query",
            "first_rank", "result_title", "result_snippet", "categories", "markets", "query_count",
        ],
        candidate_rows,
    )
    print(f"wrote {len(candidate_rows)} unique candidates to {DATA_DIR / 'candidate_domains.csv'}")
    return 0 if candidate_rows else 1


if __name__ == "__main__":
    sys.exit(main())
