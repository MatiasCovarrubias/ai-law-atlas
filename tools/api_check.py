"""Check the Claude API connection and the features the annotation runner needs, at negligible cost.

    python tools/api_check.py   # reads ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID, needed only for keys
                                # not scoped to a workspace) from .env (git-ignored) or the environment

Checks: (1) the model ID exists, (2) a plain call, (3) structured output against a JSON schema,
(4) prompt caching on a law text (a write, then a read). Prints tokens and an estimated cost for each; never
prints the key.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.environ.get("ATLAS_MODEL", "claude-haiku-5-5")
# USD per million tokens, prompts up to 100K tokens (PLAN.md §9). Cache writes assumed 1.25x input, reads 0.1x.
PRICE = {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01}


def load_key() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("No ANTHROPIC_API_KEY: put `ANTHROPIC_API_KEY=...` in ai-law-atlas/.env")


def cost(u) -> str:
    c = (u.input_tokens * PRICE["input"] + u.output_tokens * PRICE["output"]
         + (u.cache_creation_input_tokens or 0) * PRICE["cache_write"]
         + (u.cache_read_input_tokens or 0) * PRICE["cache_read"]) / 1e6
    return (f"in {u.input_tokens}, out {u.output_tokens}, cache write {u.cache_creation_input_tokens or 0}, "
            f"cache read {u.cache_read_input_tokens or 0} -> ~${c:.5f}")


def text_of(msg) -> str:
    return next((b.text for b in msg.content if b.type == "text"), "")


def main() -> None:
    load_key()
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)

    from budget import guard, record
    guard(0.05)  # the four checks cost a fraction of a cent; refuse if the budget is spent
    record(0.01, "api_check")

    print("1. Models")
    ids = [m.id for m in client.models.list(limit=100)]
    haiku = [i for i in ids if "haiku" in i]
    print(f"   haiku models available: {haiku}")
    if MODEL not in ids:
        sys.exit(f"   {MODEL} not in the model list; set ATLAS_MODEL to one of the IDs above")

    print("2. Plain call")
    msg = client.messages.create(model=MODEL, max_tokens=50,
                                 messages=[{"role": "user", "content": "Reply with the single word: ready"}])
    print(f"   reply {text_of(msg)!r}; stop {msg.stop_reason}; {cost(msg.usage)}")

    print("3. Structured output")
    schema = {"type": "object", "additionalProperties": False, "required": ["jurisdiction", "year", "topics"],
              "properties": {"jurisdiction": {"type": "string"}, "year": {"type": "integer"},
                             "topics": {"type": "array", "items": {"type": "string"}}}}
    msg = client.messages.create(
        model=MODEL, max_tokens=300,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": "Extract jurisdiction, year and topics: 'Colorado enacted a law on "
                                              "high-risk AI systems and algorithmic discrimination in 2024.'"}])
    print(f"   parsed {json.loads(text_of(msg))}; {cost(msg.usage)}")

    print("4. Prompt caching (cached law text, two requests)")
    law = (ROOT / "cache" / "texts" / "us-tx-tax-151.359.txt").read_text(encoding="utf-8")
    for i in (1, 2):
        msg = client.messages.create(
            model=MODEL, max_tokens=80,
            system=[{"type": "text", "text": "You annotate legal texts. Answer in one sentence."},
                    {"type": "text", "text": law, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": "What minimum number of qualifying jobs does the text require?"}])
        print(f"   request {i}: {text_of(msg)[:120]!r}; {cost(msg.usage)}")
    print("Done. Spend so far is a fraction of a cent; check the Console usage page to confirm.")


if __name__ == "__main__":
    main()
