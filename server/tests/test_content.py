"""Unit tests for C's content code that need no database or network."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["FAKE_AI"] = "1"

import pytest  # noqa: E402

import ai  # noqa: E402


def test_fake_brief_names_favourite_and_mover():
    race = {"position": "Men's Football Cuppers: Keble vs Worcester", "election": "Michaelmas 2026", "closes_at": "x"}
    text = ai.race_brief(race, [{"candidate": "Keble", "price": 30, "change": -8},
                                {"candidate": "Draw", "price": 20, "change": 1},
                                {"candidate": "Worcester", "price": 50, "change": 7}])
    assert text.startswith("Worcester is the most likely result at 50%")
    assert "Keble, down 8 points" in text


def test_fake_brief_steady_and_draw_label():
    race = {"position": "X: A vs B", "election": "Michaelmas 2026", "closes_at": "x"}
    text = ai.race_brief(race, [{"candidate": "Draw", "price": 60, "change": 1}, {"candidate": "A", "price": 40, "change": -1}])
    assert text == "A draw is the most likely result at 60%. The odds have been steady."


def test_fake_digest():
    races = [{"position": "C: A vs B", "closes_at": "2026-10-27T13:00:00Z",
              "candidates": [{"candidate": "A", "price": 60, "change": 5}, {"candidate": "B", "price": 40, "change": -5}]},
             {"position": "C: D vs E", "closes_at": "2026-10-26T13:00:00Z",
              "candidates": [{"candidate": "D", "price": 55, "change": 0}, {"candidate": "E", "price": 45, "change": 0}]}]
    text = ai.daily_digest(races)
    assert text.startswith("2 Cuppers matches are open for trading. Next up is D vs E, with D favoured at 55%.")
    assert "Biggest mover: A in A vs B (+5 points)." in text
    assert ai.daily_digest([]) == "No Cuppers matches are open for trading right now."
