"""Offline tests: run with  python -m unittest discover tests  (no network, no API key)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper  # noqa: E402

BASE = "https://www.gambling.com/uk/online-casinos/bonus"

CARD = """
<li>
  <a href="/go/uk/{slug}/offer/1"><img title="{name}"></a>
  <a href="/go/uk/{slug}/offer/1"><em>100%</em> up to <em>£50</em></a>
  <a href="/uk/online-casinos/reviews/{slug}">{name} Review</a>
  <p>18+. Gamble Responsibly. {terms}</p>
</li>
"""
HTML = (
    '<nav><a href="/uk/online-casinos/reviews/32red">32Red</a></nav><ul>'
    + CARD.format(slug="mr-vegas", name="Mr Vegas Casino",
                  terms="Minimum initial deposit of £10. Wager 10 times the deposit.")
    + CARD.format(slug="mega-riches", name="Mega Riches Casino", terms="Min dep £10. Wager req. 10x.")
    + CARD.format(slug="videoslots", name="Videoslots Casino", terms="Min. dep. £10. Wager req. 10x.")
    + CARD.format(slug="coral", name="Coral Casino", terms="Deposit and bet £10.")
    + "</ul>"
)


class ExtractTests(unittest.TestCase):
    def test_takes_first_three_cards_and_skips_nav(self):
        got = scraper.extract_listings(HTML, BASE)
        self.assertEqual([g["name"] for g in got], ["Mr Vegas Casino", "Mega Riches Casino", "Videoslots Casino"])
        self.assertTrue(got[0]["review_url"].endswith("/reviews/mr-vegas"))
        self.assertIn("Minimum initial deposit", got[0]["raw_text"])
        self.assertNotIn("Mega Riches", got[0]["raw_text"])


class ParseVerifyTests(unittest.TestCase):
    def test_parse_model_json_with_prose_and_string_number(self):
        raw = 'Sure! {"welcome_bonus": "100% up to £50", "wagering_requirement": "10x", "min_deposit": "£10"}'
        self.assertEqual(scraper.parse_model_json(raw)["wagering_requirement"], 10)

    def test_verify_flags_invented_numbers(self):
        text = "Min dep £10. Wager req. 10x."
        ok = {"welcome_bonus": "x", "wagering_requirement": 10, "min_deposit": "£10"}
        bad = {"welcome_bonus": "x", "wagering_requirement": 35, "min_deposit": "£20"}
        self.assertEqual(scraper.verify(ok, text), [])
        self.assertEqual(len(scraper.verify(bad, text)), 2)

    def test_no_wagering_is_not_flagged(self):
        f = {"welcome_bonus": "x", "wagering_requirement": 0, "min_deposit": None}
        self.assertEqual(scraper.verify(f, "No wagering on free spins"), [])


if __name__ == "__main__":
    unittest.main()
