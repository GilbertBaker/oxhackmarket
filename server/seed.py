"""Seed the starting markets (owner B, task B6, MVP version).

    python server/seed.py            ensure the house user + create the 10 Oxford Union markets
    python server/seed.py --list     show what would be created

Idempotent: a market whose (term, question) already exists is skipped. Every market links
to the Cherwell / Oxford Student article it came from (the article is stored in
oxmarket.articles too). Questions are forward-looking and resolve from public reporting;
none is about a private individual.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import db  # noqa: E402
import trading  # noqa: E402

TERM = "Michaelmas 2026"
CHERWELL, OXSTU = "Cherwell", "The Oxford Student"
ELECTION_CLOSE = "2026-11-26T18:00:00Z"     # before the MT26 Union election (7th week)
TERM_END = "2026-12-05T18:00:00Z"           # end of Michaelmas full term
UNION_RULES = ("Resolves on reporting in Cherwell or The Oxford Student, or official Oxford Union "
               "announcements, published by the resolution date.")

ARTICLES = {
    "fraud": (CHERWELL, "Oxford Union election count suspended amid electoral fraud allegations",
              "https://cherwell.org/2026/06/13/oxford-union-election-fraud-allegations-trinity/",
              "2026-06-13T12:00:00Z"),
    "donovan": (OXSTU, "Milo Donovan wins controversial Oxford Union Election for Hilary Term 2027",
                "https://www.oxfordstudent.com/2026/07/26/milo-donovan-wins-controversial-oxford-union-election-for-hilary-term-2027/",
                "2026-07-26T12:00:00Z"),
    "termcard": (OXSTU, "Oxford Union rescinds entire term card amidst concern from political societies",
                 "https://www.oxfordstudent.com/2026/05/16/oxford-union-rescinds-entire-term-card-amidst-concern-from-political-societies/",
                 "2026-05-16T12:00:00Z"),
    "protest": (CHERWELL, "Tommy Robinson's invitation to Oxford Union met with protest: Live updates",
                "https://cherwell.org/2026/06/17/union-west-is-right-to-be-suspicious-of-islam-debate/",
                "2026-06-17T12:00:00Z"),
    "farright": (CHERWELL, "The Oxford Union has a far-right problem",
                 "https://cherwell.org/2026/05/13/the-oxford-union-has-a-far-right-problem/",
                 "2026-05-13T12:00:00Z"),
    "banned": (CHERWELL, "Hasan Piker and Cenk Uygur denied entry to UK days before Oxford Union and Oxford Speaks events",
               "https://cherwell.org/2026/06/01/piker-and-uygur-denied-entry-to-uk-before-oxford-event/",
               "2026-06-01T12:00:00Z"),
    "town_hall": (CHERWELL, "Oxford Union town hall TT26: Meet the candidates",
                  "https://cherwell.org/2026/06/11/oxford-union-town-hall-tt26-meet-the-candidates/",
                  "2026-06-11T12:00:00Z"),
    "cost": (OXSTU, "The cost of attendance: inside the £100,000 debate",
             "https://www.oxfordstudent.com/2026/08/19/the-cost-of-attendance-inside-the-100000-debate/",
             "2026-08-19T12:00:00Z"),
    "epstein": (OXSTU, "Epstein's lawyer at the Union: is it a genocide, should 'Jane Doe' exist, and the Tories",
                "https://www.oxfordstudent.com/2026/10/14/epsteins-lawyer-at-the-union-is-it-a-genocide-should-jane-doe-exist-and-the-tories/",
                "2026-10-14T12:00:00Z"),
}

# (article key, kind, question, closes_at, priors, description)
UNION_MARKETS = [
    ("fraud", "binary", "Will the Michaelmas 2026 Oxford Union election count finish without being suspended?",
     ELECTION_CLOSE, 70, "After the Trinity 2026 count was suspended over fraud allegations."),
    ("town_hall", "multi", "How many candidates will stand for Oxford Union President in Michaelmas 2026?",
     ELECTION_CLOSE, [("1 (uncontested)", 15), ("2", 40), ("3", 30), ("4 or more", 15)],
     "Counted from the official candidate list for the MT26 presidential election."),
    ("donovan", "binary", "Will any Michaelmas 2026 Oxford Union election result be overturned or re-run after an appeal or tribunal?",
     "2027-01-10T18:00:00Z", 25, "Previous elections have been contested and controversial."),
    ("termcard", "binary", "Will the Oxford Union cancel or rescind at least one advertised Michaelmas 2026 speaker event?",
     TERM_END, 55, "In Trinity 2026 the Union rescinded its entire term card."),
    ("protest", "binary", "Will a protest outside the Oxford Union during a Michaelmas 2026 event be reported?",
     TERM_END, 75, "Several 2026 Union events drew protests outside the building."),
    ("farright", "binary", "Will a Reform UK MP or party official speak at the Oxford Union in Michaelmas 2026?",
     TERM_END, 45, "Debate over the Union's choice of speakers continues."),
    ("banned", "binary", "Will an Oxford Union speaker be prevented from attending a Michaelmas 2026 event in person?",
     TERM_END, 30, "Counts visa refusals, entry bans or travel restrictions that stop a billed speaker attending."),
    ("epstein", "binary", "Will a current or former UK Cabinet minister speak at the Oxford Union in Michaelmas 2026?",
     TERM_END, 70, "Current or former members of the UK Cabinet, speaking at a Union event."),
    ("donovan", "binary", "Will the Oxford Union publish its Hilary 2027 term card before 1 January 2027?",
     "2026-12-31T18:00:00Z", 50, "The Hilary 2027 president-elect was chosen in a controversial election."),
    ("cost", "binary", "Will the Oxford Union announce a change to its membership fee before the end of Michaelmas 2026?",
     TERM_END, 20, "The cost of Union membership has been a recurring debate."),
]


def _article_id(key):
    source, title, url, published = ARTICLES[key]
    aid = db.insert_article({"source": source, "title": title, "url": url,
                             "published_at": db.parse_iso(published), "processed": True})
    if aid is None:   # already stored (scraper or an earlier run)
        row = db._one(f"SELECT id FROM {db.SCHEMA}.articles WHERE url = %s", (url,))
        aid = row["id"]
        db.mark_article_processed(aid)
    return aid


def seed_union(dry_run=False):
    made = 0
    for key, kind, question, closes, priors, desc in UNION_MARKETS:
        if db.find_race(TERM, question):
            print(f"  exists   {question}")
            continue
        if dry_run:
            print(f"  would add [{kind}] {question}")
            continue
        source, _, url, _ = ARTICLES[key]
        cands = ([{"name": n, "prior": p} for n, p in priors] if kind == "multi" else [{"name": "Yes", "prior": priors}])
        rid = trading.create_race(TERM, question, closes, UNION_RULES, cands, kind=kind, category="union",
                                  description=desc, source_name=source, source_url=url, article_id=_article_id(key))
        db.log_admin_action(None, "POST seed.py union market", race_id=rid, details={"position": question})
        print(f"  created  #{rid} [{kind}] {question}")
        made += 1
    return made


def main(argv):
    db.ensure_schema()
    db.ensure_bot_user()
    dry = "--list" in argv
    # create_race opens engine books; outside the app that is a throwaway engine, and the
    # running app replays every open market at startup (restart it after seeding).
    n = seed_union(dry_run=dry)
    print(f"done: {n} new Oxford Union markets ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")


if __name__ == "__main__":
    main(sys.argv[1:])
