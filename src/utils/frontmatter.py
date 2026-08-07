from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    fm_text = match.group(1)
    body = content[match.end():]
    meta = {}

    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value.isdigit():
                value = int(value)
            elif value.replace(".", "", 1).isdigit():
                value = float(value)
            elif value.lower() in ("true", "false"):
                value = value.lower() == "true"
            meta[key] = value

    return meta, body


def build_frontmatter(
    doc_id: str = "auto-gen",
    source: str = "memory-workstation",
    create_utc: str = "",
    auto_label: str = "",
    memory_tier: str = "short",
    weight: int = 50,
) -> str:
    from datetime import datetime, timezone
    if not create_utc:
        create_utc = datetime.now(timezone.utc).isoformat()

    return (
        f"---\n"
        f"doc_id: {doc_id}\n"
        f"source: {source}\n"
        f"create_utc: {create_utc}\n"
        f"auto_label: {auto_label}\n"
        f"memory_tier: {memory_tier}\n"
        f"weight: {weight}\n"
        f"---\n"
    )


def extract_metadata_from_file(filepath: str) -> Optional[dict]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(2000)
        meta, _ = parse_frontmatter(content)
        return meta if meta else None
    except Exception:
        return None
