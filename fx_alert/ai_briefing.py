"""AI briefing: when an alert fires, Claude searches the web and explains why the rate moved.

Design principles
- Code computes every number (rate, % change, KRW impact) and passes them in as facts.
- The AI only explains causes. It is told not to invent numbers or make forecasts.
- Source URLs (citations) are returned so a human can verify the explanation.
- If the API key is missing or the call fails, the basic alert still goes out
  (graceful degradation).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write short FX briefings for individual investors.
Rules:
1. Use web search to find news from today or the last 1-2 days about the USD/KRW exchange rate, and identify why it moved.
2. Only use numbers that appear in the <facts> provided by the user. Never introduce new rates, percentages or amounts.
3. Do not forecast future rates and do not recommend buying or selling.
4. Only list causes you found evidence for. If you could not find any, say so.
5. Write the final result in {language}, inside <briefing> tags, in exactly this format:

<briefing>
Drivers
- (driver 1, one line)
- (driver 2, one line)
- (driver 3, one line; omit if none)
Bottom line: (what this means for the user, using only numbers from <facts>)
</briefing>"""


@dataclass
class Briefing:
    text: str
    sources: list[tuple[str, str]] = field(default_factory=list)  # (title, url)


def build_user_prompt(facts: dict) -> str:
    return (
        "The <facts> below were computed by the system and are authoritative. "
        "Write the briefing for this situation.\n\n"
        f"<facts>\n{json.dumps(facts, ensure_ascii=False, indent=2)}\n</facts>"
    )


def extract_briefing(full_text: str) -> str:
    match = re.search(r"<briefing>(.*?)</briefing>", full_text, re.DOTALL)
    return (match.group(1) if match else full_text).strip()


def parse_response(content: list) -> Briefing:
    """Join text blocks and collect unique citation URLs."""
    texts: list[str] = []
    sources: dict[str, str] = {}
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        texts.append(block.text)
        for cite in getattr(block, "citations", None) or []:
            url = getattr(cite, "url", None)
            if url and url not in sources:
                sources[url] = getattr(cite, "title", None) or url
    return Briefing(
        text=extract_briefing("".join(texts)),
        sources=[(title, url) for url, title in sources.items()],
    )


def generate_briefing(facts: dict, ai_cfg: dict) -> Briefing | None:
    if not ai_cfg.get("enabled", True):
        return None
    if not os.getenv("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY not set, skipping AI briefing.")
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=os.getenv("FX_ALERT_MODEL", ai_cfg.get("model", "claude-sonnet-5-5")),
            max_tokens=int(ai_cfg.get("max_tokens", 1200)),
            system=SYSTEM_PROMPT.format(language=ai_cfg.get("language", "English")),
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": int(ai_cfg.get("max_searches", 3)),
                }
            ],
            messages=[{"role": "user", "content": build_user_prompt(facts)}],
        )
        briefing = parse_response(response.content)
        return briefing if briefing.text else None
    except Exception as exc:  # noqa: BLE001 — an AI failure must never block the alert
        log.warning("AI briefing failed: %s", exc)
        return None
