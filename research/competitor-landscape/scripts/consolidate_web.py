#!/usr/bin/env python3
"""Consolidate generated web-search NDJSON into the analyzer's candidate CSV."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from discover import is_excluded, normalize_host


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "web_discovery.ndjson"
OUTPUT = ROOT / "data" / "candidate_domains.csv"


def main() -> None:
    grouped = {}
    query_log = []
    with SOURCE.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            batch = json.loads(line)
            results = batch.get("results", [])
            query_log.append(
                {
                    "query_number": batch["query_number"],
                    "source": "OpenAI web search",
                    "category": batch["category"],
                    "market": batch["market"],
                    "query": batch["query"],
                    "http_status": "200",
                    "result_count": len(results),
                    "retrieved_at_utc": batch["retrieved_at_utc"],
                    "error": "",
                }
            )
            for rank, result in enumerate(results, 1):
                url = result.get("url", "")
                host = normalize_host(url)
                if not host or is_excluded(host) or host == "mattkingphotography.com":
                    continue
                if url.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
                    continue
                record = grouped.setdefault(
                    host,
                    {
                        "domain": host,
                        "discovered_url": url,
                        "first_category": batch["category"],
                        "first_market": batch["market"],
                        "first_query": batch["query"],
                        "first_rank": str(rank),
                        "result_title": result.get("title", ""),
                        "result_snippet": "",
                        "categories": set(),
                        "markets": set(),
                        "queries": set(),
                    },
                )
                record["categories"].add(batch["category"])
                record["markets"].add(batch["market"])
                record["queries"].add(batch["query"])

    fields = [
        "domain", "discovered_url", "first_category", "first_market", "first_query",
        "first_rank", "result_title", "result_snippet", "categories", "markets", "query_count",
    ]
    rows = []
    for record in grouped.values():
        rows.append(
            {
                **{key: record[key] for key in fields[:8]},
                "categories": "|".join(sorted(record["categories"])),
                "markets": "|".join(sorted(record["markets"])),
                "query_count": str(len(record["queries"])),
            }
        )
    rows.sort(key=lambda item: item["domain"])
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with (ROOT / "data" / "discovery_queries.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(query_log[0]) if query_log else [])
        if query_log:
            writer.writeheader()
            writer.writerows(sorted(query_log, key=lambda item: int(item["query_number"])))
    print(f"consolidated {len(query_log)} queries into {len(rows)} unique candidate domains")


if __name__ == "__main__":
    main()
