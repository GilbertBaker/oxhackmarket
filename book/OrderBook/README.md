# C++ Limit Order Book & Market Simulator

A from-scratch limit order book and simple market simulator written in **C++20**.

I started this project primarily to learn C++ by building something more substantial than small exercises, while also applying data structures and algorithms from my Computer Science degree to a practical system. The project has since grown into an exercise in matching-engine design, correctness, profiling, and performance-driven data-structure changes.

The focus is educational rather than reproducing a production exchange.

## Features

- Separate bid and ask books organised by price.
- Market and limit buy/sell orders.
- **Price-time priority**: best price first, FIFO within each price level.
- Partial fills and execution across multiple price levels.
- Unique order IDs and order cancellation.
- Trader cash balances, share holdings, and reservation of cash/shares for resting orders.
- Removal of empty price levels from the book.
- Simple multi-trader simulation for generating market activity.
- Automated correctness tests and synthetic performance benchmarks.

## Development approach and AI use

Most of the core project code was written by me as a learning exercise: the matching logic, trader/accounting behaviour, linked-list structure, initial tree implementation, cancellation approach, and the majority of the subsequent implementation changes were developed directly while learning C++ and applying data-structure concepts from my degree.

I did use AI as a development tool, mainly in the same role as a tutor/reviewer rather than to generate the core project. Specifically, AI was used for:

- discussing design ideas and reviewing implementation decisions;
- feedback on correctness and efficiency problems;
- guidance on how to adapt my existing price tree into a balanced AVL tree;
- debugging memory-management and pointer issues;
- generating the majority of the testing and benchmarking harness code, which I then integrated with the project and used to evaluate the implementation;
- making basically all of this readme (:

I have kept this section explicit because the purpose of the repository is to show what I learned and implemented, rather than to imply that every line was written without assistance.


## Matching behaviour

For an incoming buy limit order, the engine:

1. Looks at the current best ask.
2. Executes while the best ask is at or below the buyer's limit price.
3. Fills the smaller of the incoming and resting quantities.
4. Updates both traders' cash and share balances.
5. Removes fully filled resting orders.
6. Places any unfilled remainder into the bid book.

Sell limit orders use the symmetric process against the best bid. Market orders consume available liquidity from the best price outward.

Orders at the same price are held in FIFO order, so earlier orders at that price execute first.

## Data structures

The book deliberately uses custom data structures rather than wrapping a pre-built order-book library.

### Price index — AVL tree

Each distinct price level is represented by a node in a custom **AVL tree**. The first version of the project used an ordinary unbalanced binary search tree. Benchmarking exposed its worst-case behaviour: with monotonically introduced price levels, the tree became increasingly skewed and performance degraded heavily.

I therefore changed the price index to a self-balancing AVL tree. Insertions and price-level deletions update subtree heights and use rotations to keep the tree balanced.

### Orders at one price — FIFO linked list

Each AVL node owns an `OrderList`, implemented as a doubly linked FIFO list. This separates:

- **price priority** — handled by the AVL tree;
- **time priority** — handled by FIFO ordering within a price level.

### Order lookup — hash index

The order book also maintains an order-ID index using `std::unordered_map`. This gives average-case O(1) lookup of an order's linked-list node for cancellation, rather than searching the price tree and queue for a specific order.

When the final order at a price is removed, that empty price level is removed from the AVL tree and the tree is rebalanced.

## Performance work

Performance changes were driven by measurement rather than chosen in advance.

The original unbalanced BST performed reasonably with few price levels, but in a synthetic benchmark with **1,000,000 resting-order insertions** its throughput fell to roughly **24.6k orders/s at 10,000 price levels**, and the 100,000-level case eventually caused a stack overflow because of the tree depth.

After replacing it with an AVL tree and removing empty price levels, the same style of benchmark reached approximately:

| Workload | Result |
| --- | ---: |
| 1,000,000 insertions, 10 price levels | 2.32M orders/s |
| 1,000,000 insertions, 10,000 price levels | 1.69M orders/s |
| 1,000,000 insertions, 100,000 price levels | 1.36M orders/s |
| 10,000,000 insertions, 100 price levels | 2.15M orders/s |
| 10,000,000 market-order matches, 100 price levels | 10.46M orders/s |

These are **synthetic local benchmarks from the current Debug build**, reported as the median of five runs. They measure the core implementation under controlled generated workloads; they are not intended as production-exchange performance claims. A Release build should be used for final performance reporting.

The benchmark varies both total order count and the number of distinct price levels. This was useful for distinguishing general workload scaling from behaviour caused specifically by the tree structure.

## Testing

`OrderBookTests.cpp` contains deterministic tests covering the main matching-engine properties, including:

- best bid / best ask selection;
- price priority;
- FIFO priority within one price level;
- partial fills;
- execution across multiple price levels;
- cancellation from different positions in a FIFO queue;
- invalid cancellation handling;
- conservation of total cash and shares, alongside non-negative reservation/accounting checks.

The tests are intentionally separate from the random trader simulation so matching behaviour can be checked deterministically.

## Project structure

```text
.
├── DataStructures/
│   ├── OrderBook.*          # Matching engine, accounting and order index
│   ├── OrderTree.*          # AVL tree of price levels
│   ├── OrderList.*          # FIFO list for a single price level
│   └── OrdListNode.*        # Doubly linked list node
├── Traders/
│   ├── Trader.*             # Trader state and order generation
│   └── TraderAlgo.*         # Multi-trader simulation coordinator
├── Order.*                  # Order representation
├── OrderBookTests.cpp       # Deterministic correctness tests
├── OrderBookBenchmark.cpp   # Synthetic throughput/scaling benchmarks
├── main.cpp                 # Simulation entry point
└── CMakeLists.txt
```

## Building

The project uses CMake and requires a compiler with C++20 support.

The CMake project exposes separate executables for the simulator, tests, and benchmark. In CLion these can be selected as separate run targets.

For correctness testing, a Debug build is useful because assertions remain enabled. For meaningful performance measurements, the benchmark should be run as an optimised Release build without a debugger attached.

## Scope

This is an educational matching-engine and market-simulation project, not a production trading system. It does not attempt to model networking, exchange gateways, concurrency, real market-data feeds, or production latency constraints.

The main value of the project has been using a concrete system to learn C++, implement data structures directly, test matching invariants, and use benchmarks to identify and fix real performance problems in my own implementation.
