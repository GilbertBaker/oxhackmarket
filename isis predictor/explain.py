"""Plain-English explanation of a flag forecast, written by Gemini when configured.

The prompt contains only the forecast's actual inputs and outputs (facts), and
Gemini is told to use nothing else. Without GEMINI_API_KEY in .env, or if the
API call fails, a template explanation built from the same facts is used.
"""

from urllib.request import Request, urlopen
import json
import os

import env  # noqa: F401  (loads .env)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"

PROMPT = """You explain a machine-learning forecast of the OURCs rowing flag on the Isis (River Thames, Oxford)
to student rowers. Write 2-4 short sentences in plain English, friendly and direct.

Rules:
- Use ONLY the facts in the JSON below. Do not invent observations, numbers, dates or causes.
- Say what the river is doing (flow level and trend, recent and forecast rain) and what that means for the flag.
- Mention uncertainty honestly, using the backtest accuracy given; never say a flag is certain.
- This is not official OURCs guidance; the official flag is set by OURCs.

Facts:
{facts}
"""


def gemini_configured():
    return bool(os.environ.get("GEMINI_API_KEY"))


def ask_gemini(prompt, max_tokens=400):
    """Send a prompt to Gemini and return the text reply."""
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens},
    }).encode("utf-8")
    request = Request(
        GEMINI_URL.format(model=model),
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
    )
    with urlopen(request, timeout=30) as response:
        data = json.load(response)
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def template_explanation(facts):
    river = facts["river"]
    trend = river["farmoor_flow_change_24h_pct"]
    direction = "rising" if trend > 5 else "falling" if trend < -5 else "steady"
    days = facts["forecast"]
    flags = ", ".join(f"{d['day']}: {d['flag']}" for d in days)
    return (
        f"Thames flow at Farmoor is {river['farmoor_flow_m3s']:.0f} m³/s and {direction} "
        f"({trend:+.0f}% in 24h), with {facts['rain']['observed_last_3_days_mm']:.0f} mm of rain in the last 3 days "
        f"and {facts['rain']['forecast_next_5_days_mm']:.0f} mm forecast over the next 5. "
        f"The model expects {flags}. "
        f"This is a forecast, not official OURCs guidance."
    )


def explain(facts):
    """Return (text, source) where source is 'gemini' or 'template'."""
    if gemini_configured():
        try:
            return ask_gemini(PROMPT.format(facts=json.dumps(facts, indent=2))), "gemini"
        except Exception as error:  # Network/API/quota errors: fall back rather than fail.
            print(f"Gemini unavailable ({type(error).__name__}: {error}); using template explanation")
    return template_explanation(facts), "template"
