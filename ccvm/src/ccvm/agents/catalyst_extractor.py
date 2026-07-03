"""
Catalyst extraction agent.

Uses the Claude CLI (`claude -p`) to extract structured catalyst events from
raw text (news articles, EIA releases, OPEC statements, etc.).

Requires the Claude Code CLI to be installed and authenticated (OAuth login).
No ANTHROPIC_API_KEY needed — uses the same session as Claude Code.

Rate limiting: one CLI call per document. Batch by collecting articles first,
then calling extract() in a loop.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
You are a commodity market analyst. Extract ONE primary catalyst event from the text below.
A catalyst is a concrete, dated supply or demand development that materially affects WTI crude oil prices.

Return a JSON object with exactly these fields:
{
  "event_type": one of [inventory_release, outage, opec, sanctions, refinery, weather, macro_demand, other],
  "title": "concise one-line title (max 80 chars)",
  "effective_start": "YYYY-MM-DD or null if unknown",
  "effective_end": "YYYY-MM-DD or null if open-ended",
  "commodity": "crude_oil",
  "region": "geographic region or Global",
  "direction": one of [bullish_supply, bearish_demand, two_sided, unclear],
  "magnitude": one of [low, medium, high, unknown],
  "affected_horizon": one of [prompt_1m, prompt_3m, 6m, 12m, structural],
  "source_quality": one of [primary, high_quality_secondary, other],
  "evidence": ["verbatim quote 1 (40 words max)", "verbatim quote 2"]
}

Rules:
- effective_start and effective_end must be ISO dates or null.
- direction: bullish_supply = supply reduction (price-positive); bearish_demand = demand reduction.
- magnitude: high = >$5/bbl impact potential; medium = $1-5; low = <$1.
- If no clear catalyst exists, set title to no_catalyst_found with nulls.

Return ONLY the JSON object. No explanation.

Text:
"""


def _stable_event_id(title: str, effective_start: Optional[str], event_type: str) -> str:
    """SHA-256-based stable ID for deduplication."""
    canonical = f"{event_type}|{title}|{effective_start or 'null'}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def extract(
    text: str,
    source_url: str,
    published_at: str,
    observation_date: date,
    api_key: Optional[str] = None,  # kept for API compat, unused
    model: str = "claude-haiku-4-5-20251001",
) -> Optional[dict]:
    """Extract a CatalystEvent from article text via the Claude CLI.

    Uses `claude -p` (non-interactive print mode) with the user's existing
    OAuth session — no API key required.
    Returns None if extraction fails or no catalyst is found.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        logger.error("claude CLI not found in PATH — install Claude Code")
        return None

    prompt = _EXTRACTION_PROMPT + text[:4000]

    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning("claude -p timed out for %s", source_url[:60])
        return None
    except Exception as exc:
        logger.error("Failed to run claude CLI: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning("claude -p exited %d: %s", result.returncode, result.stderr[:200])
        return None

    content = result.stdout.strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = "\n".join(content.split("\n")[1:])
        content = content.rsplit("```", 1)[0].strip()

    try:
        event = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse extraction response: %s | raw: %s", exc, content[:200])
        return None

    if event.get("title") == "no_catalyst_found":
        return None

    event["event_id"] = _stable_event_id(
        event.get("title", ""),
        event.get("effective_start"),
        event.get("event_type", "other"),
    )
    event["published_at"] = published_at
    event["observation_date"] = observation_date.isoformat()
    event["source_url"] = source_url
    event["extracted_at"] = datetime.now(timezone.utc).isoformat()
    event["extraction_model"] = "claude-cli"
    return event
