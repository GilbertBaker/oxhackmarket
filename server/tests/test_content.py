"""Unit tests for C's content code that need no database or network."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["FAKE_AI"] = "1"

import pytest  # noqa: E402

import ai  # noqa: E402
import content_api  # noqa: E402


def _body(**over):
    kick = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = {"election": "Michaelmas 2026", "position": "Men's Football Cuppers: Keble vs Worcester",
         "seats": 1, "closes_at": kick, "description": "Group A · Men's Football Cuppers",
         "candidates": [{"name": "Keble", "prior": 40}, {"name": "Draw", "prior": 25}, {"name": "Worcester", "prior": 35}]}
    b.update(over)
    return b


def test_question_text():
    pos, term = "Men's Football Cuppers: Keble vs Worcester", "Michaelmas 2026"
    assert content_api._question("Keble", pos, term) == "Will Keble beat Worcester (Men's Football Cuppers, Michaelmas 2026)?"
    assert content_api._question("Worcester", pos, term) == "Will Worcester beat Keble (Men's Football Cuppers, Michaelmas 2026)?"
    assert content_api._question("Draw", pos, term) == "Will Keble vs Worcester end in a draw (Men's Football Cuppers, Michaelmas 2026)?"
    assert content_api._question("Keble", "Rowing", term) == "Will Keble win Rowing (Michaelmas 2026)?"


def test_validate_ok():
    fields, cands = content_api._validate(_body())
    assert fields["status"] == "open" and fields["seats"] == 1
    assert fields["description"] == "Group A · Men's Football Cuppers"
    assert cands == [("Keble", 40), ("Draw", 25), ("Worcester", 35)]
    assert fields["resolution_criteria"]


@pytest.mark.parametrize("over,msg", [
    ({"election": "Autumn 2026"}, "election"),
    ({"position": "  "}, "position"),
    ({"closes_at": "2020-01-01T00:00:00Z"}, "future"),
    ({"closes_at": "2030-01-01T00:00:00"}, "timezone"),
    ({"closes_at": "soon"}, "ISO"),
    ({"candidates": [{"name": "Keble", "prior": 50}]}, "2 to 12"),
    ({"candidates": [{"name": "Keble", "prior": 50}, {"name": "keble", "prior": 50}]}, "unique"),
    ({"candidates": [{"name": "Keble", "prior": 0}, {"name": "Oriel", "prior": 50}]}, "priors"),
    ({"seats": 0}, "seats"),
])
def test_validate_rejects(over, msg):
    with pytest.raises(ValueError, match=msg):
        content_api._validate(_body(**over))


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
