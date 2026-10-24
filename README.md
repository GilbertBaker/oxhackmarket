# OxMarket

A play-money prediction market on Oxford Union elections, built at OxHack (theme: TIME).
Every candidate in every race is its own YES/NO market. Prices are in pence (1..99 = implied %).
The TimescaleDB-backed Time Machine shows who was winning each position at any moment.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
cp .env.example .env               # then fill in the DB password, SECRET_KEY, etc.
python server/app.py               # http://localhost:5000
```

On Intel Macs, `pip install --only-binary cryptography -r server/requirements.txt`
avoids a source build of `cryptography`.

## Engine & Trading (A)

_TODO (A)_

## Data & TIME (B)

_TODO (B): schema, data APIs, jobs, seed data._

## TimescaleDB usage

_TODO (B7)_

## Content, AI & Web (C)

_TODO (C)_
