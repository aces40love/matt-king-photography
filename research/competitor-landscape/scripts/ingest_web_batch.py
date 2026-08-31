#!/usr/bin/env python3
"""Append one URL-only web-search result batch to the generated discovery log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "web_discovery.ndjson"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("encoded_json")
    args = parser.parse_args()
    payload = json.loads(unquote(args.encoded_json))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("a", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")


if __name__ == "__main__":
    main()
