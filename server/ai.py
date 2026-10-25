"""Gemini (owner C): per-match "why did the odds move" briefs, the daily digest, and
market proposals from scraped Cherwell / Oxford Student articles (propose_market). Proposals
land as status 'proposed' races in the admin approval queue; nothing goes live unreviewed.

FAKE_AI=1 (or no GEMINI_API_KEY) returns deterministic text and never touches the network.
"""
import logging
import os
import threading
import time

log = logging.getLogger("ai")

BRIEF_PROMPT = """You write two to four sentences for a play-money prediction market on Oxford
college sport (Cuppers). Explain how the odds on one match have moved.
Rules: use only the numbers given below; say which outcome is favoured and which outcome
moved most, and by how much. If nothing moved by 3 points or more, say the odds are steady.
Do not invent team news, injuries, scores or reasons. Neutral tone. Plain text, no markdown.
Today (UTC): {today}
Match: {title} ({competition}, {term}), kick-off {kickoff} UTC
Outcomes (current chance %, change over the window in points):
{outcomes}
"""

DIGEST_PROMPT = """Write one short paragraph (at most 4 sentences) titled nothing, for the home page
of a play-money prediction market on Oxford college Cuppers matches. Mention the next match and
its favourite, and the biggest mover in the last 24 hours. Use only the data below; do not
invent results, scores or team news. Plain text, no markdown.
Today (UTC): {today}
Open matches (kick-off UTC | outcome chance% (24h change)):
{matches}
"""

_lock = threading.Lock()
_client = None


def _fake():
    return os.environ.get("FAKE_AI") == "1" or not os.environ.get("GEMINI_API_KEY")


def _generate(kind, prompt):
    """One Gemini call at a time, retried once. Returns text or None on failure."""
    global _client
    from google import genai  # imported lazily so FAKE_AI runs without the SDK

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    with _lock:
        if _client is None:
            _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        for attempt in (1, 2):
            t0 = time.monotonic()
            try:
                resp = _client.models.generate_content(model=model, contents=prompt)
                text = (resp.text or "").strip()
                log.info("gemini %s ok in %.1fs (%d chars)", kind, time.monotonic() - t0, len(text))
                return text or None
            except Exception as e:  # network / quota / safety block
                log.warning("gemini %s attempt %d failed after %.1fs: %s", kind, attempt, time.monotonic() - t0, e)
    return None


def _label(name):
    return "a draw" if name.strip().lower() == "draw" else name


def _split_position(position):
    comp, _, title = position.partition(": ")
    return (comp, title) if title else ("", position)


def race_brief(race, candidates_history, articles=None):
    """race: Race row. candidates_history: [{"candidate","price","change"}] where change is
    the move in points over the last ~24-72h. Returns 2-4 sentences."""
    comp, title = _split_position(race["position"])
    cands = sorted(candidates_history, key=lambda c: -c["price"])
    if not cands:
        return "No outcomes have been priced yet."
    if not _fake():
        outcomes = "\n".join(f"- {c['candidate']}: {c['price']}% ({c['change']:+d})" for c in cands)
        text = _generate("brief", BRIEF_PROMPT.format(
            today=time.strftime("%Y-%m-%d", time.gmtime()), title=title, competition=comp,
            term=race["election"], kickoff=race["closes_at"], outcomes=outcomes))
        if text:
            return text
    fav = cands[0]
    mover = max(cands, key=lambda c: abs(c["change"]))
    lead = _label(fav["candidate"])
    text = f"{lead[0].upper() + lead[1:]} is the most likely result at {fav['price']}%."
    if abs(mover["change"]) >= 3:
        text += (f" The biggest move was {_label(mover['candidate'])}, "
                 f"{'up' if mover['change'] > 0 else 'down'} {abs(mover['change'])} points.")
    else:
        text += " The odds have been steady."
    return text


def daily_digest(races):
    """races: [{"position","closes_at","candidates":[{"candidate","price","change"}]}] open matches."""
    if not races:
        return "No Cuppers matches are open for trading right now."
    races = sorted(races, key=lambda r: str(r["closes_at"]))
    if not _fake():
        lines = []
        for r in races:
            outs = ", ".join(f"{c['candidate']} {c['price']}% ({c['change']:+d})" for c in r["candidates"])
            lines.append(f"- {_split_position(r['position'])[1]} | {r['closes_at']} | {outs}")
        text = _generate("digest", DIGEST_PROMPT.format(today=time.strftime("%Y-%m-%d", time.gmtime()), matches="\n".join(lines)))
        if text:
            return text
    nxt = races[0]
    fav = max(nxt["candidates"], key=lambda c: c["price"]) if nxt["candidates"] else None
    text = f"{len(races)} Cuppers {'match is' if len(races) == 1 else 'matches are'} open for trading."
    if fav:
        text += f" Next up is {_split_position(nxt['position'])[1]}, with {_label(fav['candidate'])} favoured at {fav['price']}%."
    movers = [(c, r) for r in races for c in r["candidates"] if c["change"]]
    if movers:
        c, r = max(movers, key=lambda x: abs(x[0]["change"]))
        text += f" Biggest mover: {_label(c['candidate'])} in {_split_position(r['position'])[1]} ({c['change']:+d} points)."
    return text


PROPOSE_PROMPT = """You turn Oxford student news into questions for a PLAY-MONEY prediction
market used by Oxford students. Given one article (headline + summary), either propose ONE
question whose answer will be publicly known within 1 to 120 days, or reply with null.

Rules:
- The question must resolve unambiguously from public reporting (Cherwell, The Oxford Student,
  official University / college / Union / council announcements).
- Never make markets about named private individuals (students, junior staff), their health,
  crimes, discipline or personal lives. Public office holders acting in their role are fine.
- No markets on deaths, injuries, violence or anything distressing.
- Prefer yes/no questions ("kind": "binary"). Use "multi" only for a clear set of 2-6 options
  (e.g. candidates in an announced election); then list them in "outcomes".
- closes_at is when trading should stop (ISO 8601 UTC), before the answer is known.
- prior is your honest probability (1-99) that the answer is YES (binary), or per outcome.
- If the article is opinion, a review, a poem or has no forward-looking event, return null.

Today (UTC): {today}
Source: {source}
Headline: {title}
Summary: {summary}

Reply with JSON only: null, or
{{"question": str, "kind": "binary"|"multi", "outcomes": [{{"name": str, "prior": int}}] (multi only),
  "prior": int (binary only), "closes_at": str, "resolution_criteria": str, "category":
  "news"|"university"|"union"|"cuppers", "reason": str}}
"""


def _json_reply(text):
    import json
    import re
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except ValueError:
        log.warning("gemini proposal was not JSON: %.200s", text)
        return None


def propose_market(article):
    """article row -> a market proposal dict (see PROPOSE_PROMPT) or None.
    Without Gemini (FAKE_AI=1 / no key) returns None: an admin turns articles into markets
    by hand from the portal's Articles tab."""
    if _fake():
        return None
    text = _generate("propose", PROPOSE_PROMPT.format(
        today=time.strftime("%Y-%m-%d", time.gmtime()), source=article.get("source"),
        title=article.get("title"), summary=article.get("summary") or ""))
    data = _json_reply(text)
    if not isinstance(data, dict) or not data.get("question"):
        return None
    return data
