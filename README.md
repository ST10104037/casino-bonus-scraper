# Casino bonus scraper

Pulls the first 3 casinos from one public comparison page, uses a language model to turn the free-text bonus
terms into structured fields, writes `docs/casinos.json`, and shows it as a mobile-friendly table (`docs/index.html`).

- **Source:** https://www.gambling.com/uk/online-casinos/bonus (robots.txt checked at run time; the script stops if the page is disallowed or the site answers 401/403/429 — it never tries to get around a block).
- **Fields per casino:** `welcome_bonus`, `wagering_requirement` (multiplier), `min_deposit`, `source_url` (+ `review_url`, `raw_text`, `needs_review`).
- **Note:** this source is UK/GBP, and since Jan 2026 UK wagering is capped at 10x, so values are £ and ~10x rather than the EUR/35x of the task example.

## Install

Requires Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (Visual Studio terminal)  |  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env          # then put ONE API key in .env (ANTHROPIC_API_KEY or OPENAI_API_KEY)
```

## Run

```bash
python scraper.py               # one command: robots check -> fetch -> LLM extraction -> docs/casinos.json
python -m unittest discover tests   # optional, offline tests (no key or network needed)
```

View the page locally (it loads `casinos.json`, so it needs HTTP, not a double-click):

```bash
python -m http.server -d docs 8000   # open http://localhost:8000
```

## How it works

1. `allowed_by_robots()` reads `/robots.txt` with Python's `robotparser`.
2. `extract_listings()` finds each card via its "… Review" link and climbs to the card element (no reliance on CSS class names). Plain HTML parsing, no AI.
3. `call_llm()` sends each card's text to the model (temperature 0, "use only the text, null if not stated") and gets JSON back.
4. `verify()` checks that the numbers the model returned actually appear in the source text. If not, the row is marked `needs_review` instead of being trusted.

## Publish (GitHub Pages)

Push the repo, then Settings → Pages → Deploy from branch → `main` / `/docs`. Commit the generated `docs/casinos.json`.

## Limitations / not done

- Only the first 3 cards of one page are parsed; no pagination, no per-casino terms pages.
- If the site changes its markup, `extract_listings()` fails loudly (exit code 4) rather than guessing.
- Currency is kept as written (£), no conversion.
