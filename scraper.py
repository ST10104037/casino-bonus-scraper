#!/usr/bin/env python3
"""
Casino bonus scraper (screening task).

Run:  python scraper.py

What it does
  1. Reads robots.txt of the source site and stops if the page is not allowed.
  2. Downloads ONE public page (no logins, no CAPTCHA/block bypassing).
  3. Cuts out the first N casino listings from the HTML (plain parsing, no AI).
  4. Sends each listing's free-text terms to a language model, which returns
     the four structured fields as JSON.
  5. Cross-checks the model's numbers against the source text (guards against
     hallucination) and writes docs/casinos.json.

LLM provider: set ONE of ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY
(Gemini has a free tier), either in the environment or in a .env file next to
this script.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.gambling.com/uk/online-casinos/bonus"
NUM_CASINOS = 3
USER_AGENT = "CasinoBonusScreeningBot/1.0 (screening task; polite, 1 page, honours robots.txt)"
OUT_FILE = Path(__file__).parent / "docs" / "casinos.json"
MAX_TEXT_CHARS = 2500

SYSTEM_PROMPT = """You extract structured data from online-casino welcome-bonus text.
Use ONLY the text provided. Never guess or use outside knowledge.
Return a single JSON object, nothing else, with exactly these keys:
  "welcome_bonus": string  - the headline welcome offer as stated, e.g. "100% up to £50 + 11 Free Spins"
  "wagering_requirement": number or null - the wagering multiplier as a plain number
        (e.g. "10x" or "wager 10 times" -> 10; "no wagering" -> 0; not stated -> null)
  "min_deposit": string or null - minimum deposit with currency symbol, e.g. "£10"; null if not stated
If a value is not stated in the text, use null. Do not calculate or infer."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def load_dotenv(path: Path) -> None:
    """Minimal .env loader so there is no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fail(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# 1. robots.txt + fetch
# --------------------------------------------------------------------------- #
def allowed_by_robots(url: str) -> bool:
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=20)
    rp = robotparser.RobotFileParser()
    if resp.status_code == 200:
        rp.parse(resp.text.splitlines())
    elif resp.status_code == 404:
        return True  # no robots.txt = no restrictions
    else:
        print(f"robots.txt returned HTTP {resp.status_code}; treating as not allowed.")
        return False
    return rp.can_fetch(USER_AGENT, url)


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9"}, timeout=30)
    if resp.status_code in (401, 403, 429):
        fail(
            f"Source answered HTTP {resp.status_code} (blocked / login / rate limit). "
            "Not bypassing it - switch SOURCE_URL to another source.",
            code=3,
        )
    resp.raise_for_status()
    return decode_body(resp)


def decode_body(resp) -> str:
    """The site does not declare a charset, and `requests` then guesses Latin-1, which turns
    the pound sign into 'Â£'. Pages are UTF-8, so decode the raw bytes ourselves."""
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.text


# --------------------------------------------------------------------------- #
# 2. cut listings out of the HTML (no AI here)
# --------------------------------------------------------------------------- #
def extract_listings(html: str, base_url: str, limit: int = NUM_CASINOS) -> list[dict]:
    """
    Each casino card has a link whose text ends with "Review" pointing to
    /online-casinos/reviews/<slug>. From that link we climb to the first
    ancestor that also contains the small-print terms ("Gamble Responsibly"),
    which is the card itself. This avoids relying on volatile CSS class names.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings: list[dict] = []
    seen: set[str] = set()

    def is_review_link(tag) -> bool:
        return (
            tag.name == "a"
            and tag.get("href")
            and "/online-casinos/reviews/" in tag["href"]
            and tag.get_text(" ", strip=True).endswith("Review")
        )

    for link in soup.find_all(is_review_link):
        review_url = urljoin(base_url, link["href"])
        if review_url in seen:
            continue
        node = link
        card = None
        while node.parent is not None:
            node = node.parent
            if "Gamble Responsibly" in node.get_text(" ", strip=True):
                n_links = {a["href"] for a in node.find_all(is_review_link)}
                if len(n_links) == 1:
                    card = node
                break  # climbed into a multi-casino container -> give up on this link
        if card is None:
            continue
        seen.add(review_url)
        name = re.sub(r"\s*Review$", "", link.get_text(" ", strip=True)).strip()
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))[:MAX_TEXT_CHARS]
        listings.append({"name": name, "review_url": review_url, "raw_text": text})
        if len(listings) == limit:
            break
    return listings


# --------------------------------------------------------------------------- #
# 3. language model step
# --------------------------------------------------------------------------- #
def call_llm(casino_name: str, text: str) -> tuple[str, str]:
    """Returns (raw_model_text, model_name). Uses Anthropic if a key exists, else OpenAI."""
    user_msg = f'Casino: {casino_name}\n\nSource text:\n"""\n{text}\n"""'

    if os.getenv("ANTHROPIC_API_KEY"):
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 400,
                "temperature": 0,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"], model

    if os.getenv("OPENAI_API_KEY"):
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], model

    if os.getenv("GEMINI_API_KEY"):
        return call_gemini(SYSTEM_PROMPT + "\n\n" + user_msg)

    fail("No API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY (environment or .env file).")
    raise SystemExit  # unreachable, keeps type checkers happy


def gemini_text(data) -> str:
    """Pull the model's text out of a Gemini response (tolerates both response formats)."""
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError):
        pass
    if isinstance(data, dict) and isinstance(data.get("output_text"), str):
        return data["output_text"]
    found: list[str] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "text" and isinstance(v, str):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return "\n".join(found)


def call_gemini(prompt: str) -> tuple[str, str]:
    """Google Gemini (free tier works with an AI Studio key). Returns (text, model)."""
    env_model = os.getenv("GEMINI_MODEL")
    models = [env_model] if env_model else ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"]
    base = "https://generativelanguage.googleapis.com/v1beta"
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}
    last_error = "no attempt made"
    for model in models:
        print(f"    calling Gemini model {model} ...")
        attempts = [
            (f"{base}/models/{model}:generateContent",
             {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}),
            (f"{base}/interactions", {"model": model, "input": prompt}),
        ]
        for url, body in attempts:
            r = None
            for wait in (0, 20, 40):  # free tier is rate limited: back off on HTTP 429
                if wait:
                    print(f"    rate limited, waiting {wait}s ...")
                    time.sleep(wait)
                try:
                    r = requests.post(url, headers=headers, json=body, timeout=90)
                except requests.exceptions.RequestException as exc:
                    last_error = f"{model}: {type(exc).__name__}: {exc}"
                    print(f"    no answer from Google ({type(exc).__name__}); trying the next option ...")
                    r = None
                    break
                if r.status_code != 429:
                    break
            if r is None:
                continue
            if r.status_code in (401, 403):
                fail(f"Gemini rejected the key (HTTP {r.status_code}): {r.text[:300]}")
            if r.status_code == 200:
                text = gemini_text(r.json())
                if text:
                    return text, model
            last_error = f"{model}: HTTP {r.status_code}: {r.text[:300]}"
    fail(f"Gemini call failed. Last problem: {last_error}\n"
         "Run  python check_gemini.py  to see which models your key can use and whether Google is reachable.")
    raise SystemExit


def parse_model_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in model output: {raw[:200]!r}")
    data = json.loads(match.group(0))
    wag = data.get("wagering_requirement")
    if isinstance(wag, str):
        m = re.search(r"\d+(?:\.\d+)?", wag)
        wag = float(m.group(0)) if m else None
    if isinstance(wag, float) and wag.is_integer():
        wag = int(wag)
    return {
        "welcome_bonus": data.get("welcome_bonus"),
        "wagering_requirement": wag,
        "min_deposit": data.get("min_deposit"),
    }


# --------------------------------------------------------------------------- #
# 4. sanity check: do the model's numbers actually appear in the source?
# --------------------------------------------------------------------------- #
def number_in_text(number: str, text: str) -> bool:
    return re.search(rf"(?<![\d.,]){re.escape(number)}(?![\d])", text) is not None


def verify(fields: dict, source_text: str) -> list[str]:
    problems = []
    wag = fields["wagering_requirement"]
    if wag not in (None, 0) and not number_in_text(str(wag), source_text):
        problems.append(f"wagering {wag} not found in source text")
    dep = fields["min_deposit"]
    if dep:
        m = re.search(r"\d+(?:[.,]\d+)?", dep)
        if not m or not number_in_text(m.group(0), source_text):
            problems.append(f"min_deposit {dep!r} not found in source text")
    if not fields["welcome_bonus"]:
        problems.append("welcome_bonus empty")
    return problems


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    load_dotenv(Path(__file__).parent / ".env")

    print(f"Checking robots.txt for {SOURCE_URL} ...")
    if not allowed_by_robots(SOURCE_URL):
        fail("robots.txt does not allow fetching this page. Switch SOURCE_URL to another source.", code=2)

    print("Fetching page ...")
    html = fetch(SOURCE_URL)

    listings = extract_listings(html, SOURCE_URL)
    if len(listings) < NUM_CASINOS:
        fail(f"Found only {len(listings)} casino cards; the page layout probably changed.", code=4)
    print(f"Found {len(listings)} casinos: {', '.join(l['name'] for l in listings)}")

    results, model_used = [], None
    for item in listings:
        print(f"  LLM extraction: {item['name']} ...")
        raw, model_used = call_llm(item["name"], item["raw_text"])
        try:
            fields = parse_model_json(raw)
        except (ValueError, json.JSONDecodeError):
            raw, model_used = call_llm(item["name"], item["raw_text"])  # one retry
            fields = parse_model_json(raw)
        problems = verify(fields, item["raw_text"])
        if problems:
            print(f"    WARNING needs review: {problems}")
        results.append(
            {
                "name": item["name"],
                "welcome_bonus": fields["welcome_bonus"],
                "wagering_requirement": fields["wagering_requirement"],
                "min_deposit": fields["min_deposit"],
                "source_url": SOURCE_URL,
                "review_url": item["review_url"],
                "needs_review": bool(problems),
                "review_notes": problems,
                "raw_text": item["raw_text"],
            }
        )
        time.sleep(0.5)

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_URL,
        "model": model_used,
        "casinos": results,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()