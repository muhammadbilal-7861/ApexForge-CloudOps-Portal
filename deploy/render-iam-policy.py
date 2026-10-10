#!/usr/bin/env python3
"""Render scoped policy templates against private inventory; never apply them."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from cloudops_config import environment, load


def render(template: dict, config: dict) -> dict:
    values = environment(config)

    def expand(value):
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, str):
            def replace(match):
                key = match[1]
                if key not in values:
                    raise ValueError("unknown policy configuration field")
                return values[key]
            return re.sub(r"\$\{([A-Z_]+)\}", replace, value)
        return value

    return expand(template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = render(json.loads(args.template.read_text()), load())
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    args.output.chmod(0o600)
    print("Scoped policy rendered privately; owner review/application required.")


if __name__ == "__main__":
    main()
