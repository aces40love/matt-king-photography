#!/usr/bin/env python3
"""Print a deterministic slice of the discovery query plan as JSON."""

from __future__ import annotations

import argparse
import json

from discover import make_queries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int)
    parser.add_argument("count", type=int)
    parser.add_argument("--seed", type=int, default=20260831)
    args = parser.parse_args()
    queries = make_queries(args.seed)
    selected = queries[args.start : args.start + args.count]
    print(
        json.dumps(
            [
                {
                    "query_number": args.start + index + 1,
                    "category": category,
                    "market": market,
                    "query": query,
                }
                for index, (category, market, query) in enumerate(selected)
            ]
        )
    )


if __name__ == "__main__":
    main()
