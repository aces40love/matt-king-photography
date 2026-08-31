#!/usr/bin/env python3
"""Fetch and analyze competitor homepages without executing JavaScript.

Only the initial HTML document is requested. Linked scripts, stylesheets, images,
fonts, and tracking pixels are not fetched. Results are reproducible as a dated
snapshot but will naturally change as sites and networks change.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from lxml import etree, html


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
    "CompetitorLandscapeResearch/1.0"
)
MAX_HTML_BYTES = 4_000_000
THREAD_STATE = threading.local()

FIELDS = [
    "domain", "homepage_url", "final_url", "http_status", "retrieved_at_utc", "response_ms",
    "html_bytes", "content_type", "is_live_html", "is_relevant", "rejection_reason",
    "title", "meta_description", "h1", "word_count", "detected_categories",
    "source_categories", "source_markets", "source_query", "search_result_title",
    "search_result_snippet", "platform", "uses_https", "has_viewport", "has_schema_local_business",
    "has_local_seo_signal", "has_pricing_language", "has_visible_price", "has_action_cta",
    "has_booking", "has_gallery", "has_testimonials", "has_process", "has_faq", "has_about",
    "has_full_service_products", "image_count", "lazy_image_count", "srcset_image_count",
    "images_missing_alt", "images_with_dimensions", "script_count", "stylesheet_count",
    "internal_link_count", "external_link_count", "fetch_error",
]

CATEGORY_PATTERNS = {
    "portrait": r"\bportrait(?:s|ure)?\b",
    "family": r"\bfamil(?:y|ies)\b|family portraits?",
    "senior": r"\b(?:high school|graduating|graduation|class of) seniors?\b|\bsenior portraits?\b",
    "children": r"\bchild(?:ren|'s)?\b|\bkids?\b|\bmilestone\b",
    "newborn_maternity": r"\bnewborns?\b|\bbab(?:y|ies)\b|\bmaternity\b|\bmotherhood\b",
    "fine_art": r"\bfine[- ]art\b|\bpainterly\b|\beditorial portraits?\b",
    "full_service": r"\bfull[- ]service\b|\bwall art\b|\bheirloom\b|\bordering appointment\b|\breveal session\b",
}

NEGATIVE_PATTERNS = [
    r"photography (?:school|course|tutorial|tips|news|magazine|museum|forum)",
    r"camera (?:reviews?|gear|store)",
    r"royalty[- ]free|stock (?:photos?|images?)|download free images",
    r"directory of photographers|find (?:the )?best photographers|compare photographers",
    r"photo (?:printing|lab|editor|editing software|hosting)",
    r"domain (?:is )?for sale|buy this domain|parked free",
]

CHALLENGE_PATTERNS = [
    "enable javascript and cookies to continue", "checking your browser", "verify you are human",
    "attention required! | cloudflare", "access denied", "bot verification", "captcha",
]


def session() -> requests.Session:
    if not hasattr(THREAD_STATE, "session"):
        value = requests.Session()
        value.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en-US,en;q=0.8",
                "Cache-Control": "no-cache",
            }
        )
        THREAD_STATE.session = value
    return THREAD_STATE.session


def bool_text(value: bool) -> str:
    return "1" if value else "0"


def clean_text(value: str, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


def text_of(nodes: Sequence[etree._Element], limit: int = 500) -> str:
    if not nodes:
        return ""
    return clean_text(nodes[0].text_content(), limit)


def attr_content(doc: html.HtmlElement, xpath: str, limit: int = 500) -> str:
    nodes = doc.xpath(xpath)
    if not nodes:
        return ""
    return clean_text(str(nodes[0]), limit)


def contains(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, flags=re.I))


def detect_platform(raw_lower: str) -> str:
    signatures = [
        ("Showit", ("showit", "showit.co")),
        ("Squarespace", ("squarespace", "static1.squarespace.com")),
        ("Wix", ("wixstatic.com", "wix.com/website/builder", "x-wix-")),
        ("WordPress", ("wp-content/", "wp-includes/", "wordpress")),
        ("Pixieset", ("pixieset.com", "pixieset website")),
        ("SmugMug", ("smugmug.com", "smugmugcdn.com")),
        ("Webflow", ("webflow.com", "data-wf-page", "webflow.js")),
        ("Zenfolio", ("zenfolio.com", "zenfolio")),
        ("PhotoBiz", ("photobiz.com", "photobiz")),
        ("ShootProof", ("shootproof.com", "shootproof")),
        ("Format", ("format.com", "format-assets")),
        ("Duda", ("duda.co", "d1di2lzuh97fh2.cloudfront")),
        ("Shopify", ("cdn.shopify.com", "shopify-section")),
        ("Flothemes", ("flothemes", "flo-launch")),
    ]
    for name, markers in signatures:
        if any(marker in raw_lower for marker in markers):
            return name
    return "Other/unknown"


def candidate_urls(domain: str, discovered_url: str) -> List[str]:
    discovered = urlparse(discovered_url)
    host = discovered.hostname or domain
    urls = [f"https://{host}/", f"http://{host}/"]
    if host.startswith("www."):
        urls += [f"https://{domain}/", f"http://{domain}/"]
    return list(dict.fromkeys(urls))


def fetch_html(domain: str, discovered_url: str) -> Tuple[Dict[str, object], bytes]:
    errors = []
    for url in candidate_urls(domain, discovered_url):
        started = time.monotonic()
        try:
            response = session().get(url, timeout=(6, 18), allow_redirects=True, stream=True)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            content_type = response.headers.get("Content-Type", "")
            chunks: List[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                remaining = MAX_HTML_BYTES - total
                if remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
                if total >= MAX_HTML_BYTES:
                    break
            response.close()
            body = b"".join(chunks)
            looks_html = (
                "html" in content_type.lower()
                or body.lstrip()[:100].lower().startswith((b"<!doctype html", b"<html", b"<?xml"))
            )
            metadata = {
                "homepage_url": url,
                "final_url": response.url,
                "http_status": response.status_code,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "response_ms": elapsed_ms,
                "html_bytes": len(body),
                "content_type": content_type[:120],
                "looks_html": looks_html,
                "fetch_error": " | ".join(errors),
            }
            if response.status_code == 200 and looks_html and len(body) >= 300:
                return metadata, body
            errors.append(f"{url}: HTTP {response.status_code}, {len(body)} bytes")
        except requests.RequestException as exc:
            errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:160]}")
    return (
        {
            "homepage_url": candidate_urls(domain, discovered_url)[0],
            "final_url": "",
            "http_status": 0,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "response_ms": 0,
            "html_bytes": 0,
            "content_type": "",
            "looks_html": False,
            "fetch_error": " | ".join(errors)[:1500],
        },
        b"",
    )


def analyze_candidate(source: Dict[str, str]) -> Dict[str, str]:
    domain = source["domain"]
    fetched, body = fetch_html(domain, source["discovered_url"])
    base: Dict[str, str] = {field: "" for field in FIELDS}
    base.update(
        {
            "domain": domain,
            "homepage_url": str(fetched["homepage_url"]),
            "final_url": str(fetched["final_url"]),
            "http_status": str(fetched["http_status"]),
            "retrieved_at_utc": str(fetched["retrieved_at_utc"]),
            "response_ms": str(fetched["response_ms"]),
            "html_bytes": str(fetched["html_bytes"]),
            "content_type": str(fetched["content_type"]),
            "is_live_html": bool_text(bool(body)),
            "source_categories": source.get("categories", ""),
            "source_markets": source.get("markets", ""),
            "source_query": source.get("first_query", ""),
            "search_result_title": source.get("result_title", ""),
            "search_result_snippet": source.get("result_snippet", ""),
            "fetch_error": str(fetched["fetch_error"]),
        }
    )
    if not body:
        base["is_relevant"] = "0"
        base["rejection_reason"] = "not_live_static_html"
        return base

    try:
        doc = html.fromstring(body, base_url=str(fetched["final_url"]))
    except (ValueError, etree.ParserError) as exc:
        base["is_relevant"] = "0"
        base["rejection_reason"] = "html_parse_error"
        base["fetch_error"] = f"{base['fetch_error']} | {type(exc).__name__}: {exc}".strip(" |")
        return base

    raw = body.decode("utf-8", "replace")
    raw_lower = raw.lower()
    title = attr_content(doc, "//title/text()", 300)
    description = attr_content(
        doc,
        "//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='description']/@content",
        500,
    )
    if not description:
        description = attr_content(doc, "//meta[@property='og:description']/@content", 500)
    h1 = text_of(doc.xpath("//h1"), 400)

    # Make visible-text analysis independent from embedded scripts and CSS.
    for node in doc.xpath("//script|//style|//noscript|//template|//svg"):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    visible = clean_text(doc.text_content(), 300_000)
    visible_lower = visible.lower()
    # Query wording is discovery provenance, not relevance evidence. Only the
    # site's own HTML and the indexed result title/snippet may satisfy the filter.
    search_context = " ".join(
        [source.get("result_title", ""), source.get("result_snippet", "")]
    ).lower()
    combined = " ".join([domain.lower(), title.lower(), description.lower(), h1.lower(), visible_lower, search_context])

    links = doc.xpath("//a[@href]")
    link_text = " ".join(clean_text(anchor.text_content(), 100) for anchor in links).lower()
    action_text = " ".join([visible_lower, link_text])
    images = doc.xpath("//img")
    scripts = doc.xpath("//script[@src]")  # doc was pruned; use raw count below instead
    script_count = len(re.findall(r"<script\b", raw, flags=re.I))
    stylesheets = len(re.findall(r"<link\b[^>]*rel=[\"'][^\"']*stylesheet", raw, flags=re.I))

    pricing_language = contains(r"\b(pricing|investment|session fee|collections?|packages?)\b", action_text)
    visible_price = contains(r"(?:[$£€]\s?\d[\d,.]*|\b\d[\d,.]*\s?(?:usd|cad|aud|gbp)\b)", visible)
    action_cta = contains(
        r"\b(book(?: now| a session)?|inquire|enquire|contact(?: me| us)?|schedule|reserve|"
        r"get started|let'?s (?:talk|chat|connect)|plan your session|apply now)\b",
        action_text,
    )
    booking = contains(r"\b(book now|book a session|schedule|reserve your|availability)\b", action_text)
    gallery = contains(r"\b(galler(?:y|ies)|portfolio|view (?:the )?(?:work|images)|featured work)\b", action_text)
    testimonials = contains(r"\b(testimonials?|reviews?|client love|kind words|what clients say)\b", action_text)
    process = contains(r"\b(the process|our process|my process|what to expect|your experience|the experience|how it works)\b", action_text)
    faq = contains(r"\bfaq\b|frequently asked questions|common questions", action_text)
    about = contains(r"\babout (?:me|us)|meet (?:the )?photographer|our story|my story\b", action_text)
    full_service = contains(
        r"\b(wall art|albums?|heirloom|prints?|framed|artwork|ordering appointment|"
        r"design consultation|reveal session|finished products?)\b",
        visible_lower,
    )

    detected_categories = [name for name, pattern in CATEGORY_PATTERNS.items() if contains(pattern, combined)]
    photo_evidence = contains(r"\b(photograph(?:er|ers|y|ies|ic)?|portraits?|photo sessions?|portrait studio)\b", combined)
    business_evidence = action_cta or gallery or about or contains(
        r"\b(?:specializing|specialising|serving|based in|located in|photography studio)\b", combined
    )
    negative_hits = [pattern for pattern in NEGATIVE_PATTERNS if contains(pattern, combined)]
    challenge = any(marker in visible_lower[:5000] for marker in CHALLENGE_PATTERNS)

    source_market = source.get("first_market", "")
    market_words = source_market.split()
    city = " ".join(market_words[:-1]) if len(market_words) > 1 else source_market
    head_text = " ".join([title, description, h1]).lower()
    city_signal = bool(city and city.lower() in head_text)
    schema_local = contains(
        r"[\"']@type[\"']\s*:\s*[\"'](?:localbusiness|professionalservice|"
        r"photographybusiness|store)[\"']|[\"']postaladdress[\"']",
        raw_lower,
    )
    local_seo = city_signal or schema_local or contains(r"\bserving\b.{0,100}\b(area|city|county|families)\b", visible_lower)

    internal_links = 0
    external_links = 0
    final_host = (urlparse(str(fetched["final_url"])).hostname or domain).lower().removeprefix("www.")
    for anchor in links:
        href = anchor.get("href", "")
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        resolved_host = (urlparse(urljoin(str(fetched["final_url"]), href)).hostname or "").lower().removeprefix("www.")
        if not resolved_host or resolved_host == final_host:
            internal_links += 1
        else:
            external_links += 1

    blocked_or_parked = challenge or contains(r"domain (?:is )?for sale|buy this domain|parked free", combined)
    relevant = bool(photo_evidence and detected_categories and business_evidence and not blocked_or_parked and len(negative_hits) < 2)
    rejection = ""
    if not relevant:
        if blocked_or_parked:
            rejection = "challenge_or_parked"
        elif not photo_evidence:
            rejection = "no_photography_evidence"
        elif not detected_categories:
            rejection = "no_target_category_evidence"
        elif not business_evidence:
            rejection = "no_business_evidence"
        elif len(negative_hits) >= 2:
            rejection = "directory_education_or_stock"
        else:
            rejection = "relevance_filter"

    words = re.findall(r"\b[\w'-]+\b", visible)
    lazy_images = sum(1 for image in images if image.get("loading", "").lower() == "lazy")
    srcset_images = sum(1 for image in images if image.get("srcset") or image.get("data-srcset"))
    missing_alt = sum(1 for image in images if not clean_text(image.get("alt", "")))
    dimensioned = sum(1 for image in images if image.get("width") and image.get("height"))

    base.update(
        {
            "is_relevant": bool_text(relevant),
            "rejection_reason": rejection,
            "title": title,
            "meta_description": description,
            "h1": h1,
            "word_count": str(len(words)),
            "detected_categories": "|".join(detected_categories),
            "platform": detect_platform(raw_lower),
            "uses_https": bool_text(str(fetched["final_url"]).startswith("https://")),
            "has_viewport": bool_text(contains(r"<meta\b[^>]*name=[\"']viewport[\"']", raw_lower)),
            "has_schema_local_business": bool_text(schema_local),
            "has_local_seo_signal": bool_text(local_seo),
            "has_pricing_language": bool_text(pricing_language),
            "has_visible_price": bool_text(visible_price),
            "has_action_cta": bool_text(action_cta),
            "has_booking": bool_text(booking),
            "has_gallery": bool_text(gallery),
            "has_testimonials": bool_text(testimonials),
            "has_process": bool_text(process),
            "has_faq": bool_text(faq),
            "has_about": bool_text(about),
            "has_full_service_products": bool_text(full_service),
            "image_count": str(len(images)),
            "lazy_image_count": str(lazy_images),
            "srcset_image_count": str(srcset_images),
            "images_missing_alt": str(missing_alt),
            "images_with_dimensions": str(dimensioned),
            "script_count": str(script_count),
            "stylesheet_count": str(stylesheets),
            "internal_link_count": str(internal_links),
            "external_link_count": str(external_links),
        }
    )
    return base


def read_candidates(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[Dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def pct(count: int, total: int) -> float:
    return round(100 * count / total, 1) if total else 0.0


def build_summary(rows: List[Dict[str, str]], relevant: List[Dict[str, str]]) -> Dict[str, object]:
    total = len(relevant)
    signals = [
        "has_pricing_language", "has_visible_price", "has_action_cta", "has_booking", "has_gallery",
        "has_testimonials", "has_process", "has_faq", "has_about", "has_full_service_products",
        "has_local_seo_signal", "has_schema_local_business", "uses_https", "has_viewport",
    ]
    signal_summary = {
        name: {"count": sum(row[name] == "1" for row in relevant), "percent": pct(sum(row[name] == "1" for row in relevant), total)}
        for name in signals
    }
    platforms = Counter(row["platform"] for row in relevant)
    categories: Counter[str] = Counter()
    for row in relevant:
        categories.update(filter(None, row["detected_categories"].split("|")))
    html_sizes = sorted(int(row["html_bytes"] or 0) for row in relevant)
    response_times = sorted(int(row["response_ms"] or 0) for row in relevant)
    image_counts = sorted(int(row["image_count"] or 0) for row in relevant)

    def median(values: List[int]) -> int:
        return values[len(values) // 2] if values else 0

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_domains": len(rows),
        "live_static_html": sum(row["is_live_html"] == "1" for row in rows),
        "relevant_live_competitors": total,
        "signal_prevalence": signal_summary,
        "platforms": dict(platforms.most_common()),
        "categories": dict(categories.most_common()),
        "medians": {
            "initial_html_bytes": median(html_sizes),
            "homepage_response_ms": median(response_times),
            "static_image_tag_count": median(image_counts),
        },
        "rejection_reasons": dict(Counter(row["rejection_reason"] for row in rows if row["is_relevant"] != "1").most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DATA_DIR / "candidate_domains.csv")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    candidates = read_candidates(args.input)
    if args.limit:
        candidates = candidates[: args.limit]
    print(f"analyzing {len(candidates)} candidate domains with {args.workers} workers", flush=True)

    rows: List[Dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(analyze_candidate, candidate): candidate["domain"] for candidate in candidates}
        for index, future in enumerate(as_completed(futures), 1):
            domain = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # Preserve the rest of a large crawl if one parser edge case fails.
                failed = {field: "" for field in FIELDS}
                failed.update(
                    {
                        "domain": domain,
                        "is_live_html": "0",
                        "is_relevant": "0",
                        "rejection_reason": "analysis_exception",
                        "fetch_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                rows.append(failed)
            if index % 100 == 0 or index == len(candidates):
                live = sum(row["is_live_html"] == "1" for row in rows)
                relevant = sum(row["is_relevant"] == "1" for row in rows)
                print(f"completed={index}/{len(candidates)} live={live} relevant={relevant}", flush=True)

    rows.sort(key=lambda row: row["domain"])
    relevant_rows = [row for row in rows if row["is_relevant"] == "1"]
    write_csv(DATA_DIR / "crawl_all.csv", rows)
    write_csv(DATA_DIR / "competitors.csv", relevant_rows)
    summary = build_summary(rows, relevant_rows)
    with (DATA_DIR / "aggregate_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"wrote {len(relevant_rows)} relevant live competitors to {DATA_DIR / 'competitors.csv'}")
    return 0 if len(relevant_rows) >= 1000 else 2


if __name__ == "__main__":
    sys.exit(main())
