"""Pure risk / collateral rules (no DB access). All money in pence, YES terms.

Collateral rule:
  - a short YES position of q shares locks 100*q (worst case it resolves YES)
  - an open buy order locks price*qty_remaining
  - an open sell order locks (100-price) per share NOT covered by a long position
"""
from collections import defaultdict


class RiskError(Exception):
    pass


def locked_cash(positions, open_orders):
    pos = defaultdict(int)
    for p in positions:
        pos[p["market_id"]] += int(p["qty"])

    locked = 0
    for q in pos.values():
        locked += 100 * max(0, -q)

    sells = defaultdict(list)
    for o in open_orders:
        if o["side"] == "buy":
            locked += int(o["price"]) * int(o["qty_remaining"])
        else:
            sells[o["market_id"]].append(o)

    for market_id, orders in sells.items():
        # long shares cover the most expensive-to-collateralise (lowest price) sells first
        cover = max(0, pos.get(market_id, 0))
        for o in sorted(orders, key=lambda o: int(o["price"])):
            qty = int(o["qty_remaining"])
            covered = min(cover, qty)
            cover -= covered
            locked += (100 - int(o["price"])) * (qty - covered)
    return locked


def available(cash, positions, open_orders):
    return int(cash) - locked_cash(positions, open_orders)


def check_order(cash, positions, open_orders, market_id, side, price, qty):
    """Raise RiskError if adding this (limit) order would make available cash negative."""
    before = available(cash, positions, open_orders)
    hypo = list(open_orders) + [{"market_id": market_id, "side": side, "price": price, "qty_remaining": qty}]
    after = available(cash, positions, hypo)
    if after < 0:
        need = before - after
        raise RiskError(f"Insufficient funds: need {need}, available {max(0, before)}")


def worst_case_market_cost(book_side_levels, qty, side="buy", long_qty=0):
    """Walk the opposite side of the book for a market order.
    book_side_levels = asks for a buy, bids for a sell, best first.
    Returns (cost, fillable_qty). For a buy, cost = sum price*qty. For a sell,
    cost = collateral (100-price) per share not covered by `long_qty`."""
    cost, filled = 0, 0
    cover = max(0, long_qty)
    for lvl in book_side_levels:
        if filled >= qty:
            break
        n = min(qty - filled, int(lvl["qty"]))
        p = int(lvl["price"])
        if side == "buy":
            cost += p * n
        else:
            covered = min(cover, n)
            cover -= covered
            cost += (100 - p) * (n - covered)
        filled += n
    return cost, filled


def max_affordable_market_qty(cash, positions, open_orders, market_id, side, levels, qty):
    """Largest q <= qty that is fillable from `levels` and keeps available >= 0.
    The market order is modelled as limit orders at each level's price."""
    fillable = sum(int(l["qty"]) for l in levels)
    hi = min(qty, fillable)

    def ok(q):
        hypo, left = list(open_orders), q
        for lvl in levels:
            if left <= 0:
                break
            n = min(left, int(lvl["qty"]))
            hypo.append({"market_id": market_id, "side": side, "price": int(lvl["price"]), "qty_remaining": n})
            left -= n
        return available(cash, positions, hypo) >= 0

    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo, fillable
