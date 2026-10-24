#include <cassert>
#include <iostream>

#include "DataStructures/OrderBook.h"
#include "Traders/TraderAlgo.h"
#include "Traders/Trader.h"
#include "Order.h"

struct Fixture {
    OrderBook book;
    TraderAlgo algo;

    Fixture() : book(), algo(&book) {
        book.algo = &algo;
    }
};

Order* makeOrder(
    TraderAlgo& algo,
    int price,
    int quantity,
    int traderID,
    bool buy
) {
    return new Order(
        price,
        quantity,
        0,
        traderID,
        algo.getNextID(),
        buy
    );
}


void testBestPrices() {
    Fixture f;

    f.book.placeBuyOrder(makeOrder(f.algo, 1000, 10, 1, true));
    f.book.placeBuyOrder(makeOrder(f.algo, 990, 10, 2, true));

    f.book.placeSellOrder(makeOrder(f.algo, 1020, 10, 3, false));
    f.book.placeSellOrder(makeOrder(f.algo, 1030, 10, 4, false));

    assert(f.book.getBestBid() == 1000);
    assert(f.book.getBestAsk() == 1020);
}


void testPricePriority() {
    Fixture f;

    Trader* buyer = f.algo.getTrader(1);
    int initialShares = buyer->qOwned;

    // Cheaper ask arrives second.
    f.book.placeSellOrder(
        makeOrder(f.algo, 1020, 10, 2, false)
    );

    f.book.placeSellOrder(
        makeOrder(f.algo, 1010, 10, 3, false)
    );

    f.book.placeBuyInstantOrder(10, 1);

    // Buyer must receive the cheaper order first.
    assert(buyer->qOwned == initialShares + 10);
    assert(f.book.getBestAsk() == 1020);
}


void testFIFO() {
    Fixture f;

    Trader* firstSeller = f.algo.getTrader(1);
    Trader* secondSeller = f.algo.getTrader(2);

    int firstInitial = firstSeller->qOwned;
    int secondInitial = secondSeller->qOwned;

    f.book.placeSellOrder(
        makeOrder(f.algo, 1000, 10, 1, false)
    );

    f.book.placeSellOrder(
        makeOrder(f.algo, 1000, 10, 2, false)
    );

    f.book.placeBuyInstantOrder(10, 3);

    // Same price -> first order must execute first.
    assert(firstSeller->qOwned == firstInitial - 10);
    assert(secondSeller->qOwned == secondInitial);
}


void testPartialFill() {
    Fixture f;

    f.book.placeSellOrder(
        makeOrder(f.algo, 1000, 10, 1, false)
    );

    // Only 4 of 10 execute.
    f.book.placeBuyInstantOrder(4, 2);

    assert(f.book.getBestAsk() == 1000);

    // Remaining 6 execute.
    f.book.placeBuyInstantOrder(6, 2);

    assert(f.book.getBestAsk() == -1);
}


void testMultiplePriceLevels() {
    Fixture f;

    f.book.placeSellOrder(
        makeOrder(f.algo, 1000, 5, 1, false)
    );

    f.book.placeSellOrder(
        makeOrder(f.algo, 1010, 10, 2, false)
    );

    f.book.placeSellOrder(
        makeOrder(f.algo, 1020, 10, 3, false)
    );

    // Executes:
    // 5 @ 1000
    // 7 @ 1010
    f.book.placeBuyInstantOrder(12, 4);

    assert(f.book.getBestAsk() == 1010);
}


void testCancelHeadMiddleTail() {
    Fixture f;

    Trader* trader = f.algo.getTrader(1);

    auto addBuy = [&]() {
        Order* order = makeOrder(f.algo, 1000, 10, 1, true);
        int id = order->orderID;

        f.book.placeBuyOrder(order);

        return id;
    };

    int first = addBuy();
    int second = addBuy();
    int third = addBuy();

    assert(trader->currentOrders.size() == 3);

    // Middle
    f.book.cancelOrder(second);
    assert(trader->currentOrders.size() == 2);

    // Head
    f.book.cancelOrder(first);
    assert(trader->currentOrders.size() == 1);

    // Tail
    f.book.cancelOrder(third);
    assert(trader->currentOrders.empty());

    assert(f.book.getBestBid() == -1);
}


void testInvalidCancellation() {
    Fixture f;

    f.book.cancelOrder(123456789);

    assert(f.book.getBestBid() == -1);
    assert(f.book.getBestAsk() == -1);
}


void testAccountingConservation() {
    Fixture f;

    long long totalCashBefore = 0;
    long long totalSharesBefore = 0;

    for (int i = 0; i < 100; ++i) {
        Trader* trader = f.algo.getTrader(i);

        totalCashBefore += trader->money;
        totalSharesBefore += trader->qOwned;
    }

    f.book.placeSellOrder(
        makeOrder(f.algo, 1000, 10, 1, false)
    );

    f.book.placeBuyInstantOrder(10, 2);

    long long totalCashAfter = 0;
    long long totalSharesAfter = 0;

    for (int i = 0; i < 100; ++i) {
        Trader* trader = f.algo.getTrader(i);

        totalCashAfter += trader->money;
        totalSharesAfter += trader->qOwned;

        assert(trader->money >= 0);
        assert(trader->qOwned >= 0);
        assert(trader->reservedMoney >= 0);
        assert(trader->reservedQuantity >= 0);
        assert(trader->reservedMoney <= trader->money);
        assert(trader->reservedQuantity <= trader->qOwned);
    }

    assert(totalCashBefore == totalCashAfter);
    assert(totalSharesBefore == totalSharesAfter);
}


void testFillsRecorded() {
    Fixture f;

    f.book.placeSellOrder(new Order(50, 10, 0, 1, 101, false));
    f.book.placeSellOrder(new Order(51, 10, 0, 2, 102, false));

    f.book.fills.clear();
    f.book.placeBuyOrder(new Order(55, 15, 0, 3, 103, true));

    assert(f.book.fills.size() == 2);
    assert(f.book.fills[0].makerOrderID == 101);
    assert(f.book.fills[0].makerTraderID == 1);
    assert(f.book.fills[0].takerTraderID == 3);
    assert(f.book.fills[0].price == 50);
    assert(f.book.fills[0].qty == 10);
    assert(f.book.fills[0].takerIsBuy);
    assert(f.book.fills[1].makerOrderID == 102);
    assert(f.book.fills[1].price == 51);
    assert(f.book.fills[1].qty == 5);
    assert(f.book.getRestingQty(102) == 5);
    assert(f.book.getRestingQty(103) == 0);

    f.book.fills.clear();
    f.book.placeBuyInstantOrder(3, 4);
    assert(f.book.fills.size() == 1);
    assert(f.book.fills[0].makerOrderID == 102);
    assert(f.book.fills[0].qty == 3);
    assert(f.book.getRestingQty(102) == 2);
}

void testUnknownTrader() {
    OrderBook book;
    TraderAlgo algo(&book, 0);
    book.algo = &algo;

    book.placeBuyOrder(new Order(40, 5, 0, 5000, 1, true));
    book.placeSellOrder(new Order(40, 5, 0, 6000, 2, false));

    assert(book.fills.size() == 1);
    assert(book.fills[0].makerTraderID == 5000);
    assert(book.fills[0].takerTraderID == 6000);
    assert(!book.fills[0].takerIsBuy);
}

void testDepth() {
    Fixture f;

    f.book.placeBuyOrder(new Order(40, 5, 0, 1, 1, true));
    f.book.placeBuyOrder(new Order(42, 5, 0, 1, 2, true));
    f.book.placeBuyOrder(new Order(42, 7, 0, 2, 3, true));
    f.book.placeBuyOrder(new Order(38, 1, 0, 2, 4, true));
    f.book.placeSellOrder(new Order(60, 3, 0, 1, 5, false));
    f.book.placeSellOrder(new Order(55, 4, 0, 1, 6, false));

    auto bids = f.book.getDepth(true, 2);
    assert(bids.size() == 2);
    assert(bids[0] == std::make_pair(42, 12));
    assert(bids[1] == std::make_pair(40, 5));

    auto asks = f.book.getDepth(false, 10);
    assert(asks.size() == 2);
    assert(asks[0] == std::make_pair(55, 4));
    assert(asks[1] == std::make_pair(60, 3));

    //cancelling the only order at a level removes the level
    f.book.cancelOrder(6);
    asks = f.book.getDepth(false, 10);
    assert(asks.size() == 1);
    assert(asks[0] == std::make_pair(60, 3));
}


int main() {
    testBestPrices();
    testPricePriority();
    testFIFO();
    testPartialFill();
    testMultiplePriceLevels();
    testCancelHeadMiddleTail();
    testInvalidCancellation();
    testAccountingConservation();
    testFillsRecorded();
    testUnknownTrader();
    testDepth();

    std::cout << "All tests passed.\n";

    return 0;
}