# OxHack simple starter

Plain **HTML + CSS + JavaScript** frontend, **Python** backend. No framework, `pip install`, `npm install`, or database.

## 1. Install Python (Windows)

If you don't already have Python, install it from <https://www.python.org/downloads/windows/>. If the installer offers **Add python.exe to PATH**, enable it. Open a **new** terminal afterwards.

In PowerShell or Command Prompt, check:

```powershell
py --version
```

If `py` doesn't exist, try `python --version` instead.

## 2. Run it

Unzip the project, open a terminal **inside the `oxhack-simple-starter` folder**, then run:

```powershell
py backend/server.py
```

Or, if `py` is unavailable:

```powershell
python backend/server.py
```

You can also double-click **start.bat** in File Explorer.

Open **http://127.0.0.1:8000** in your browser. **Keep the terminal open.** To stop the server, press **Ctrl+C**.

**You do not need npm at all** for this template.

## Files

```text
oxhack-simple-starter/
├── backend/
│   ├── server.py          # One Python backend AND web server
│   ├── predict.py         # Flag prediction (served at /api/flag)
│   └── conditions.py      # Hourly Isis weather (Met Office) + OURCs flag, served at /api/conditions
├── frontend/
│   ├── index.html         # Web page
│   ├── style.css          # Appearance
│   └── script.js          # Browser logic / API calls
├── start.bat              # Optional Windows launcher
├── .gitignore
└── README.md
```

## How it works

1. Python serves `frontend/index.html`, CSS, and JS at `http://127.0.0.1:8000/`.
2. The browser sends a POST request to `/api/message` when you click **Send**.
3. The Python code handles the request and returns JSON, which JavaScript shows on the page.
4. `GET /api/health` checks that the backend is running.

To build your hackathon idea, edit **`frontend/index.html`**, **`frontend/style.css`**, **`frontend/script.js`** and the `/api/message` handler inside **`backend/server.py`**. Add more `if path == "/api/..."` routes as needed.

This is a **local development starter**, not a production-ready public server: it has no authentication, persistent storage, or deployment configuration.

## Flag forecast (1-5 days ahead)

The site's forecast comes from a TensorFlow model trained on river flow, rain and past flags.

Setup, once:

```powershell
py -m pip install -r requirements.txt
```

Create `.env` in the project folder (it is git-ignored, never commit it):

```text
DATABASE_URL=postgres://tsdbadmin:PASSWORD@HOST:PORT/tsdb?sslmode=require
GEMINI_API_KEY=your-key            # optional: plain-English explanations by Gemini
GEMINI_MODEL=gemini-2.5-flash      # optional
```

Without `DATABASE_URL` everything runs from `data/*.csv`; without `GEMINI_API_KEY` explanations use a template.

| Command | What it does |
|---|---|
| `py fetch_data.py` | Downloads EA river data and Open-Meteo rain forecasts to `data/` and uploads them to Tiger Data |
| `py neuralnet.py` | Trains the model ensemble, saves it to `model/` |
| `py experiments.py` | Backtests every horizon against "copy today's flag", writes `model/evaluation.json` |
| `py forecast.py` | Issues today's forecast (`model/latest_forecast.json` and Tiger Data `flag_forecasts`) |

The server reissues the forecast automatically after 07:00 each day (`/api/forecast`).
Files: `features.py` (inputs + no-lookahead check), `tigerdb.py` (TimescaleDB storage),
`explain.py` (Gemini), `forecast.py` (live forecast), `backend/predict.py` (serves it).

## Share through GitHub

Create a new empty GitHub repository, then from this folder run:

```powershell
git init
git add .
git commit -m "Add minimal HTML and Python starter"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git
git push -u origin main
```

Git must be installed separately (see <https://git-scm.com/downloads/win>) unless you're using GitHub Desktop. Replace the URL with your own new repository's HTTPS address.
