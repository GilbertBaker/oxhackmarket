# STUB created by B. Owner A replaces this file wholesale.
"""Minimal fake of the trading service (COLLAB 7.1) so the app boots."""
import db


class TradingError(Exception):
    pass


def startup():
    pass


def start_background():
    pass


def open_market(market_id):
    pass


def place_order(user_id, market_id, outcome, side, type, price, qty):
    raise TradingError("trading engine not available yet (stub)")


def cancel_order(user_id, order_id, is_admin=False):
    raise TradingError("trading engine not available yet (stub)")


def open_race(race_id):
    pass


def close_market(market_id):
    db.update_market(market_id, status="closed")


def close_race(race_id):
    db.update_race(race_id, status="closed")


def resolve_market(market_id, outcome):
    raise TradingError("trading engine not available yet (stub)")


def resolve_race(race_id, winner_market_ids):
    raise TradingError("trading engine not available yet (stub)")


def get_book(market_id, depth=10):
    return {"bids": [], "asks": []}


def available_cash(user_id):
    user = db.get_user(user_id)
    return user["cash"] if user else 0
