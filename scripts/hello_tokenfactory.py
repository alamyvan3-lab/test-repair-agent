"""Discover Nemotron models on Nebius Token Factory and make one tiny call per role.

Usage:
    python scripts/hello_tokenfactory.py            # list models, call MODEL_ULTRA / MODEL_NANO
    python scripts/hello_tokenfactory.py --json out.json

Reads TOKEN_FACTORY_API_KEY (and optional TOKEN_FACTORY_BASE_URL, ULTRA_BASE_URL,
NANO_BASE_URL, MODEL_ULTRA, MODEL_NANO) from the environment or .env. If the model IDs are
unset, it suggests Nemotron IDs from the live /v1/models listing and calls those.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

import openai
from dotenv import load_dotenv

from test_repair.config import DEFAULT_BASE_URL, Settings
from test_repair.llm import list_models

# A second regional endpoint used in Nebius's own Nemotron cookbook examples.
REGIONAL_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"


def pick(ids: list[str], prefer: list[str]) -> str:
    nemotron = [i for i in ids if "nemotron" in i.lower()]
    for word in prefer:
        hits = sorted(i for i in nemotron if word in i.lower())
        if hits:
            return hits[0]
    return ""


def call(base_url: str, api_key: str, model: str) -> dict:
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=0)
    start = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word OK."}],
        temperature=0,
        max_tokens=512,
    )
    latency = time.perf_counter() - start
    usage = resp.usage
    return {
        "model": resp.model or model,
        "base_url": base_url,
        "latency_s": round(latency, 3),
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "finish_reason": resp.choices[0].finish_reason,
        "reply": (resp.choices[0].message.content or "").strip()[:200],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", help="also write the measurements to this file")
    args = parser.parse_args()

    load_dotenv(".env")
    s = Settings.from_env(env_file=None)
    if not s.api_key:
        print("TOKEN_FACTORY_API_KEY is not set. Copy .env.example to .env and add your key.")
        return 2

    listings: dict[str, list[dict]] = {}
    for url in dict.fromkeys([s.ultra.base_url, s.nano.base_url, DEFAULT_BASE_URL,
                              REGIONAL_BASE_URL]):
        try:
            listings[url] = list_models(url, s.api_key)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"[{url}] model listing failed: {exc}")
            continue
        nemo = [m for m in listings[url] if "nemotron" in m.get("id", "").lower()]
        print(f"\n[{url}] {len(listings[url])} models, {len(nemo)} Nemotron:")
        for m in nemo:
            price = m.get("pricing") or {}
            print(f"  {m['id']:<50} context={m.get('context_length', '?')} "
                  f"status={m.get('status', '?')} pricing={json.dumps(price) if price else '?'}")

    def locate(model: str, preferred: str) -> str:
        for url in [preferred, *listings]:
            if any(m.get("id") == model for m in listings.get(url, [])):
                return url
        return preferred

    all_ids = [m.get("id", "") for models in listings.values() for m in models]
    ultra = s.ultra.model or pick(all_ids, ["ultra", "super"])
    nano = s.nano.model or pick(all_ids, ["nano", "super"])
    if not s.ultra.model or not s.nano.model:
        print("\nSuggested .env lines (from the live listing above):")
        print(f"MODEL_ULTRA={ultra}\nMODEL_NANO={nano}")
    if not ultra or not nano:
        print("No Nemotron model found for one of the roles; see the listing above.")
        return 1

    results = {}
    for role, model, preferred in (("ultra", ultra, s.ultra.base_url),
                                   ("nano", nano, s.nano.base_url)):
        url = locate(model, preferred)
        if url != preferred:
            print(f"\nNote: {model} is listed at {url}; set {role.upper()}_BASE_URL={url}")
        try:
            results[role] = call(url, s.api_key, model)
        except openai.APIError as exc:
            print(f"\n{role}: call to {model} failed: {type(exc).__name__}: {exc}")
            continue
        r = results[role]
        print(f"\n{role}: {r['model']}\n  latency {r['latency_s']}s, prompt_tokens "
              f"{r['prompt_tokens']}, completion_tokens {r['completion_tokens']}, "
              f"finish {r['finish_reason']}, reply {r['reply']!r}")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8", newline="\n")
        print(f"\nWrote {args.json}")
    return 0 if len(results) == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
