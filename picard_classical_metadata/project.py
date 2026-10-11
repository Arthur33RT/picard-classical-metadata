#!/usr/bin/env python3
"""Project a V2.5 SEMANTIC_MAP.json into per-recording classical tags."""

import argparse
import json
import sys
from pathlib import Path

from core import SemanticMapProjector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("semantic_map", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.semantic_map.read_text(encoding="utf-8"))
    projection = SemanticMapProjector(data).project_release()
    rendered = json.dumps(projection, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        # Windows consoles frequently retain a legacy code page even though
        # MusicBrainz data is Unicode.  Write explicit UTF-8 bytes so names and
        # punctuation are never rejected or silently transliterated.
        sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
