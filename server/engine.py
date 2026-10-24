"""Bridge to the C++ matching engine (book/OrderBook MarketServer.exe).

Speaks the line protocol in plan/COLLAB.txt section 8. One subprocess serves
every market. All calls are serialised by `Engine.lock` (re-entrant, so
trading.py can hold it across a whole order placement).

If ENGINE_PATH doesn't exist, a pure-Python book with the same protocol is used
instead (logged loudly) so the app still runs on machines that haven't built
the C++ yet. Set ENGINE_PATH correctly for the demo.
"""
import logging
import os
import subprocess
import threading
from collections import deque

log = logging.getLogger("engine")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join("book", "OrderBook", "build", "MarketServer.exe")


class EngineError(Exception):
    """The engine answered ERR <reason>. `reason` is the protocol token."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class EngineDied(Exception):
    pass


def _resolve_path(path):
    path = path or os.environ.get("ENGINE_PATH") or DEFAULT_PATH
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    return path


class Engine:
    def __init__(self, path=None, replay_hook=None):
        self.path = _resolve_path(path)
        self.replay_hook = replay_hook   # called with this engine after a restart
        self.lock = threading.RLock()
        self.proc = None
        self.py = None                   # PyEngine fallback
        self._replaying = False

    # ---------------------------------------------------------------- process
    def start(self):
        with self.lock:
            if not os.path.exists(self.path):
                log.error("ENGINE_PATH %s not found: using the pure-Python fallback book. "
                          "Build book/OrderBook (target MarketServer) for the real engine.", self.path)
                self.py = PyEngine()
                return
            self.proc = subprocess.Popen(
                [self.path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                cwd=os.path.dirname(self.path))
            first = self.proc.stdout.readline().strip()
            if first != "READY":
                raise EngineDied(f"engine did not print READY (got {first!r})")
            log.info("engine started: %s (pid %s)", self.path, self.proc.pid)

    def stop(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write("QUIT\n")
                    self.proc.stdin.flush()
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
            self.proc = None
            self.py = None

    def kill(self):
        """Kill the subprocess without cleanup (tests use this to simulate a crash)."""
        if self.proc:
            self.proc.kill()
            self.proc.wait()

    def restart(self):
        """Fresh engine (empty books), then rebuild state via the replay hook."""
        with self.lock:
            log.warning("restarting engine")
            try:
                self.stop()
            except Exception:
                pass
            self.start()
            if self.replay_hook and not self._replaying:
                self._replaying = True
                try:
                    self.replay_hook(self)
                finally:
                    self._replaying = False

    def running(self):
        return self.py is not None or (self.proc is not None and self.proc.poll() is None)

    # ------------------------------------------------------------------- raw
    def _send_raw(self, cmd):
        if self.py is not None:
            return self.py.handle(cmd)
        if self.proc is None or self.proc.poll() is not None:
            raise EngineDied("engine not running")
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
            lines = []
            while True:
                line = self.proc.stdout.readline()
                if line == "":
                    raise EngineDied("EOF from engine")
                line = line.strip()
                if line == "END":
                    return lines
                lines.append(line)
        except (BrokenPipeError, OSError) as e:
            raise EngineDied(str(e))

    def send(self, cmd):
        """Send one command, return response lines (without END). Raises EngineError on ERR."""
        with self.lock:
            log.debug("> %s", cmd)
            try:
                lines = self._send_raw(cmd)
            except EngineDied as e:
                if self._replaying:
                    raise
                log.error("engine died (%s), restarting and retrying %r", e, cmd)
                self.restart()
                lines = self._send_raw(cmd)
            log.debug("< %s", lines)
            if lines and lines[0].startswith("ERR"):
                raise EngineError(lines[0][4:].strip() or "unknown")
            return lines

    # --------------------------------------------------------------- helpers
    def ping(self):
        return self.send("PING") == ["PONG"]

    def new_market(self, mkt):
        self.send(f"NEW {int(mkt)}")

    def limit(self, mkt, order_id, trader, side, price, qty):
        """side 'buy'|'sell' (YES). Returns (fills, resting_qty).
        fills = [{"maker_order_id","maker_trader_id","price","qty"}]"""
        s = "B" if side == "buy" else "S"
        lines = self.send(f"LIMIT {int(mkt)} {int(order_id)} {int(trader)} {s} {int(price)} {int(qty)}")
        fills, resting = _parse_fills(lines, "RESTING")
        return fills, resting

    def market(self, mkt, trader, side, qty):
        """Returns (fills, unfilled_qty)."""
        s = "B" if side == "buy" else "S"
        lines = self.send(f"MARKET {int(mkt)} {int(trader)} {s} {int(qty)}")
        return _parse_fills(lines, "UNFILLED")

    def cancel(self, mkt, order_id):
        """True if cancelled, False if the order wasn't resting."""
        try:
            self.send(f"CANCEL {int(mkt)} {int(order_id)}")
            return True
        except EngineError as e:
            if e.reason == "not_found":
                return False
            raise

    def book(self, mkt, depth=10):
        """{"bids":[{"price","qty"}], "asks":[...]} best first, YES terms."""
        out = {"bids": [], "asks": []}
        for line in self.send(f"BOOK {int(mkt)} {int(depth)}"):
            kind, price, qty = line.split()
            out["bids" if kind == "BID" else "asks"].append({"price": int(price), "qty": int(qty)})
        return out


def _parse_fills(lines, tail_key):
    fills, tail = [], 0
    for line in lines:
        parts = line.split()
        if parts[0] == "FILL":
            fills.append({"maker_order_id": int(parts[1]), "maker_trader_id": int(parts[2]),
                          "price": int(parts[3]), "qty": int(parts[4])})
        elif parts[0] == tail_key:
            tail = int(parts[1])
    return fills, tail


# ---------------------------------------------------------------------------
# Pure-Python fallback with identical protocol semantics (price-time priority,
# trades at the resting price). Only used when the exe is missing.
# ---------------------------------------------------------------------------
class _PyBook:
    def __init__(self):
        self.levels = {"B": {}, "S": {}}   # side -> price -> deque[[order_id, trader, qty]]
        self.index = {}                    # order_id -> (side, price)

    def best(self, side):
        lv = self.levels[side]
        if not lv:
            return None
        return max(lv) if side == "B" else min(lv)

    def match(self, taker, side, qty, limit_price=None):
        fills = []
        opp = "S" if side == "B" else "B"
        while qty > 0:
            p = self.best(opp)
            if p is None:
                break
            if limit_price is not None and ((side == "B" and p > limit_price) or (side == "S" and p < limit_price)):
                break
            q = self.levels[opp][p]
            maker = q[0]
            n = min(qty, maker[2])
            fills.append((maker[0], maker[1], p, n))
            qty -= n
            maker[2] -= n
            if maker[2] == 0:
                q.popleft()
                del self.index[maker[0]]
                if not q:
                    del self.levels[opp][p]
        return fills, qty

    def rest(self, order_id, trader, side, price, qty):
        self.levels[side].setdefault(price, deque()).append([order_id, trader, qty])
        self.index[order_id] = (side, price)

    def resting_qty(self, order_id):
        if order_id not in self.index:
            return 0
        side, price = self.index[order_id]
        for o in self.levels[side][price]:
            if o[0] == order_id:
                return o[2]
        return 0

    def cancel(self, order_id):
        side, price = self.index.pop(order_id)
        q = self.levels[side][price]
        for o in list(q):
            if o[0] == order_id:
                q.remove(o)
        if not q:
            del self.levels[side][price]

    def depth(self, side, n):
        prices = sorted(self.levels[side], reverse=(side == "B"))[:n]
        return [(p, sum(o[2] for o in self.levels[side][p])) for p in prices]


class PyEngine:
    def __init__(self):
        self.books = {}

    def handle(self, cmd):
        try:
            return self._handle(cmd.split())
        except (ValueError, IndexError):
            return ["ERR bad_command"]

    def _handle(self, a):
        c = a[0]
        if c == "PING":
            return ["PONG"]
        if c == "NEW" and len(a) == 2:
            self.books.setdefault(int(a[1]), _PyBook())
            return ["OK"]
        if c == "LIMIT" and len(a) == 7 and a[4] in ("B", "S"):
            mkt, oid, trader, side, price, qty = int(a[1]), int(a[2]), int(a[3]), a[4], int(a[5]), int(a[6])
            b = self.books.get(mkt)
            if b is None:
                return ["ERR unknown_market"]
            if not (1 <= price <= 99) or qty <= 0:
                return ["ERR bad_args"]
            if b.resting_qty(oid) > 0:
                return ["ERR duplicate_id"]
            fills, left = b.match(trader, side, qty, price)
            if left > 0:
                b.rest(oid, trader, side, price, left)
            return [f"FILL {f[0]} {f[1]} {f[2]} {f[3]}" for f in fills] + [f"RESTING {left}"]
        if c == "MARKET" and len(a) == 5 and a[3] in ("B", "S"):
            mkt, trader, side, qty = int(a[1]), int(a[2]), a[3], int(a[4])
            b = self.books.get(mkt)
            if b is None:
                return ["ERR unknown_market"]
            if qty <= 0:
                return ["ERR bad_args"]
            fills, left = b.match(trader, side, qty)
            return [f"FILL {f[0]} {f[1]} {f[2]} {f[3]}" for f in fills] + [f"UNFILLED {left}"]
        if c == "CANCEL" and len(a) == 3:
            b = self.books.get(int(a[1]))
            if b is None:
                return ["ERR unknown_market"]
            if b.resting_qty(int(a[2])) <= 0:
                return ["ERR not_found"]
            b.cancel(int(a[2]))
            return ["OK"]
        if c == "BOOK" and len(a) == 3:
            b = self.books.get(int(a[1]))
            if b is None:
                return ["ERR unknown_market"]
            n = int(a[2])
            if n <= 0:
                return ["ERR bad_args"]
            return [f"BID {p} {q}" for p, q in b.depth("B", n)] + [f"ASK {p} {q}" for p, q in b.depth("S", n)]
        return ["ERR bad_command"]


_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """Module-level singleton. trading.startup() sets the replay hook and starts it."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine
