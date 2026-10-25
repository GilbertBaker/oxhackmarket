"""Admin portal validation and settings checks that need no database (validate_race takes
its reference data as an argument). The HTTP routes are tested against the real DB in
tests/db/test_db_admin.py."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["FAKE_AI"] = "1"

import pytest  # noqa: E402

import admin_api  # noqa: E402

REF = {
    "terms": {"Michaelmas 2026", "Hilary 2027"},
    "competitions": {"Men's Football Cuppers": {"name": "Men's Football Cuppers", "has_draw": True, "active": True},
                     "Old Cuppers": {"name": "Old Cuppers", "has_draw": False, "active": False}},
    "categories": {"cuppers", "news", "union", "other"},
    "criteria": {"match": "match rules", "generic": "generic rules"},
}
KICK = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def match(**over):
    b = {"kind": "match", "election": "Michaelmas 2026", "position": "Men's Football Cuppers: Keble vs Worcester",
         "closes_at": KICK, "description": "Group A",
         "candidates": [{"name": "Keble", "prior": 40}, {"name": "Draw", "prior": 25}, {"name": "Worcester", "prior": 35}]}
    b.update(over)
    return b


def binary(**over):
    b = {"kind": "binary", "election": "Michaelmas 2026", "position": "Will the Union motion pass?",
         "closes_at": KICK, "prior": 30, "category": "union"}
    b.update(over)
    return b


def multi(**over):
    b = {"kind": "multi", "election": "Hilary 2027", "position": "Who will win the OUSU presidency?",
         "closes_at": KICK, "seats": 1,
         "candidates": [{"name": "A", "prior": 50}, {"name": "B", "prior": 30}, {"name": "C", "prior": 20}]}
    b.update(over)
    return b


def test_match_ok():
    f = admin_api.validate_race(match(), REF)
    assert f["kind"] == "match" and f["category"] == "cuppers" and f["seats"] == 1 and f["status"] == "open"
    assert f["candidates"] == [{"name": "Keble", "prior": 40}, {"name": "Draw", "prior": 25},
                               {"name": "Worcester", "prior": 35}]
    assert f["resolution_criteria"] == "match rules" and f["description"] == "Group A"


def test_binary_ok_and_draft():
    f = admin_api.validate_race(binary(status="proposed"), REF)
    assert f["candidates"] == [{"name": "Yes", "prior": 30}] and f["seats"] == 1
    assert f["status"] == "proposed" and f["category"] == "union" and f["resolution_criteria"] == "generic rules"


def test_multi_ok_default_category():
    f = admin_api.validate_race(multi(), REF)
    assert f["kind"] == "multi" and f["category"] == "news" and len(f["candidates"]) == 3


@pytest.mark.parametrize("body,msg", [
    (match(election="Autumn 2026"), "election"),
    (match(position="  "), "position"),
    (match(closes_at="2020-01-01T00:00:00Z"), "future"),
    (match(closes_at="2030-01-01T00:00:00"), "timezone"),
    (match(closes_at="soon"), "ISO"),
    (match(candidates=[{"name": "Keble", "prior": 50}]), "2 to 12"),
    (match(candidates=[{"name": "Keble", "prior": 50}, {"name": "keble", "prior": 50}]), "unique"),
    (match(candidates=[{"name": "Keble", "prior": 0}, {"name": "Worcester", "prior": 50}]), "priors"),
    (match(seats=0), "seats"),
    (match(position="Women's Darts: Keble vs Worcester"), "unknown competition"),
    (match(position="Old Cuppers: Keble vs Worcester"), "not active"),
    (match(position="Keble beats Worcester"), "Competition"),
    (match(candidates=[{"name": "Oriel", "prior": 50}, {"name": "Worcester", "prior": 50}]), "home and away"),
    (match(category="sport"), "category"),
    (match(status="resolved"), "status"),
    (match(source_url="javascript:alert(1)"), "source_url"),
    (match(kind="poll"), "kind"),
    (binary(prior=100), "prior"),
    (binary(prior="50"), "prior"),
    (multi(seats=3), "fewer"),
])
def test_validate_rejects(body, msg):
    with pytest.raises(ValueError, match=msg):
        admin_api.validate_race(body, REF)


@pytest.mark.parametrize("key,value,ok", [
    ("mm.size", 25, True), ("mm.size", 0, False), ("mm.size", "25", False), ("mm.size", True, False),
    ("mm.alpha", 0.5, True), ("mm.alpha", 2, False),
    ("defaults.priors.draw", [40, 20, 40], True), ("defaults.priors.draw", [50, 50], False),
    ("defaults.criteria.match", "  rules ", True), ("defaults.criteria.match", "", False),
    ("nope.key", 1, False),
])
def test_check_setting(key, value, ok):
    if ok:
        admin_api._check_setting(key, value)
    else:
        with pytest.raises(ValueError):
            admin_api._check_setting(key, value)


def test_check_ref():
    assert admin_api._check_ref("teams", {"name": "  New   Hall "}, partial=False) == {"name": "New Hall"}
    with pytest.raises(ValueError, match="missing"):
        admin_api._check_ref("terms", {"name": "Hilary 2028"}, partial=False)
    with pytest.raises(ValueError, match="before"):
        admin_api._check_ref("terms", {"name": "X", "starts_on": "2027-03-01", "ends_on": "2027-01-01"}, partial=False)
    with pytest.raises(ValueError, match="regular expression"):
        admin_api._check_ref("sports", {"pattern": "("}, partial=True)
    with pytest.raises(ValueError, match="slug"):
        admin_api._check_ref("categories", {"slug": "Bad Slug", "name": "x"}, partial=False)
    with pytest.raises(ValueError, match="unknown field"):
        admin_api._check_ref("teams", {"id": 3}, partial=True)
