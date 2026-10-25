# How the Order Book Works: slide notes

Each slide has **bullets for the slide** and **speaker notes** for the presenter.

---

## Slide 1: What is an order book?

- A list of everyone who wants to **buy** (bids) and everyone who wants to **sell** (asks)
- Each market (one YES/NO question) has its own book
- Prices run from **1 to 99**, read as a probability in %. A YES share pays **100** if YES wins and **0** if not
- The engine pairs buyers with sellers. It does not set the price

**Speaker notes:** A price of 62 means the market puts YES at about 62%. The engine never invents a price. Every trade happens at a price that someone already chose to post.

---

## Slide 2: Architecture

```
Browser ──▶ Python server (trading.py) ──stdin/stdout──▶ MarketServer.exe (C++)
                 │                                         one book per market
                 ▼
             Postgres (cash, positions, orders, trades)
```

- **The C++ engine only does matching.** It doesn't know about money
- **Python and Postgres own the money.** They check risk before an order goes in and write every fill after it comes back
- They talk over a plain-text line protocol: `NEW`, `LIMIT`, `MARKET`, `CANCEL`, `BOOK`, `PING`
- If the exe is missing, a pure-Python book with the same behaviour takes over

**Speaker notes:** Keeping the engine small and fast makes it easy to test. The database stays the source of truth for balances. `server/engine.py` starts the exe as a subprocess and sends one command per line. The engine answers with lines and finishes with `END`.

---

## Slide 3: Order types

| Type | What it says | What happens |
|---|---|---|
| **Limit** | "Buy 10 at 60 or better" | Matches what it can now. The rest **rests** in the book |
| **Market** | "Buy 10 at whatever the price is" | Sweeps the best prices on offer. Any part that can't fill is dropped, never rested |
| **Cancel** | "Pull my order #123" | Takes a resting order out of the book |

**Speaker notes:** A resting order is called a *maker* because it adds liquidity. The incoming order that hits it is the *taker*.

---

## Slide 4: Matching rule, price-time priority

1. **Best price first.** A buyer hits the **lowest ask** first and a seller hits the **highest bid** first
2. **First come, first served at the same price (FIFO).** The order that arrived earliest fills first
3. **The trade happens at the resting order's price.** The taker gets any price improvement

**Speaker notes:** If I post a buy at 65 and the best ask is 60, I pay 60, not 65. This is the same rule that real stock exchanges use.

---

## Slide 5: Worked example

Book before:

| Bids (buy YES) | | Asks (sell YES) |
|---|---|---|
| 58 × 20 | | **60 × 5** (Alice) |
| 55 × 40 | | 60 × 10 (Bob) |
| | | 63 × 30 |

**Incoming: Limit BUY 25 @ 62**

- Fill 5 @ 60 against Alice, who was first at 60
- Fill 10 @ 60 against Bob
- The next ask is 63, which is above our limit of 62, so matching stops
- The remaining 10 **rests as a new best bid: 62 × 10**

**Speaker notes:** Walk through the loop. Look at the best opposite price, check it's within the limit, fill `min(my qty, their qty)`, remove the maker if it's fully filled, and repeat. The engine returns `FILL` lines for Alice and Bob, then `RESTING 10`.

---

## Slide 6: The matching loop (from `OrderBook.cpp`)

```cpp
while (q > 0) {
    Order* best = sellOrders->getLowestOrder();
    if (best == nullptr || best->price > order->price) break;  // empty, or too expensive
    int fill = std::min(q, best->quantity);
    fills.push_back({best->orderID, best->id, order->id, best->price, fill, true});
    q -= fill;
    best->quantity -= fill;
    if (best->quantity == 0) deleteOrder(best->orderID);
}
if (q > 0) rest the remainder in buyOrders;
```

- A sell is the mirror image: it walks `buyOrders->getHighestOrder()`
- A market order uses the same loop without the price check

---

## Slide 7: Data structures

```
OrderTree (AVL tree, keyed by price)
   ├── 55 ──▶ OrderList: [ord] ⇄ [ord] ⇄ [ord]   (FIFO, doubly linked)
   ├── 58 ──▶ OrderList: [ord]
   └── 60 ──▶ OrderList: [Alice] ⇄ [Bob]

unordered_map<orderID, OrdListNode*>   → finds any order directly for cancel
```

- **AVL tree** of price levels gives **price priority**. Finding the best price is O(log n), and the tree stays balanced
- **Doubly linked list** at each price gives **time priority**. Adding to the back and removing from the front are O(1)
- **Hash map** from order ID to list node makes **cancel O(1)** on average, with no searching
- When a price level becomes empty, it's removed from the tree and the tree is rebalanced

**Speaker notes:** The first version used a plain, unbalanced binary search tree. When prices arrived in rising order it turned into a long chain, throughput fell to about 25k orders/s, and at 100k levels it overflowed the stack. The AVL tree fixed it.

---

## Slide 8: Performance (synthetic benchmarks, Debug build)

| Workload | Throughput |
|---|---:|
| 1M inserts, 10 price levels | 2.32M orders/s |
| 1M inserts, 10,000 price levels | 1.69M orders/s |
| 1M inserts, 100,000 price levels | 1.36M orders/s |
| 10M market-order matches, 100 levels | **10.46M orders/s** |

- With the old unbalanced tree, 10,000 levels managed only about 24.6k orders/s

**Speaker notes:** These are local synthetic numbers, not exchange-grade claims. They show that the switch to AVL was driven by measurement.

---

## Slide 9: YES and NO share one book

- The engine only stores a **YES** book
- **Buy NO @ p = Sell YES @ (100 − p)**
- **Sell NO @ p = Buy YES @ (100 − p)**
- Example: buying NO at 30 goes into the book as selling YES at 70

**Speaker notes:** Because NO is just the other side of YES, there's one pool of liquidity and the two prices always add up to 100. Python translates the order in `trading._to_yes` before it reaches the engine.

---

## Slide 10: Life of an order (`trading.place_order`)

1. **Validate:** outcome, side, type, qty, and price between 1 and 99
2. **Convert to YES terms**
3. **Risk check:** the order must be covered by cash that isn't already locked
   - An open buy locks price × qty
   - An open sell locks (100 − price) per share that the trader doesn't already own
   - A short YES position locks 100 per share
4. **Send to the engine** and get back the fills plus the resting or unfilled quantity
5. **Persist everything in one database transaction:** trades, cash, positions, maker order status, the new order, and a price tick
6. If the database write fails, **restart the engine and replay the open orders from the database**

**Speaker notes:** The whole sequence runs under a single engine lock. Each order's risk check therefore sees the previous order's committed cash. The database is the source of truth, so the in-memory book can always be rebuilt from it.

---

## Slide 11: Liquidity and settlement

- **House market maker** (`mm_bot.py`): every 10 seconds it quotes 50 shares at fair ± 2 in every open market
  - Before the first trade, fair value is the AI's starting estimate (the prior). After trading starts, it's a moving average of trade prices pulled toward that estimate and skewed against the house's own holdings
- **Settlement:** when a market resolves YES, each YES share pays 100. When it resolves NO, YES shares pay 0. Positions are then closed out

**Speaker notes:** The bot means a user always has something to trade against, even in a brand-new market.

---

## Slide 12: Summary

- Price-time priority, with trades at the maker's price
- AVL tree for prices, FIFO linked lists for time, and a hash map for cancels
- The C++ engine matches orders. Python and Postgres handle money and keep the records
- One YES book also serves NO trades through 100 − p
- The engine can be restarted and rebuilt from the database
