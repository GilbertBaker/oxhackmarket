#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iomanip>
#include <iostream>
#include <vector>

#include "DataStructures/OrderBook.h"
#include "Traders/TraderAlgo.h"
#include "Traders/Trader.h"
#include "Order.h"


struct Fixture {
    OrderBook book;
    TraderAlgo algo;

    Fixture() : book(), algo(&book) {
        book.algo = &algo;

        // Prevent trader resources from limiting the benchmark.
        for (int i = 0; i < 100; ++i) {
            Trader* trader = algo.getTrader(i);
            trader->money = 1'000'000'000;
            trader->qOwned = 1'000'000'000;
            trader->reservedMoney = 0;
            trader->reservedQuantity = 0;
        }
    }
};


double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());

    const std::size_t n = values.size();

    if (n % 2 == 0) {
        return (values[n / 2 - 1] + values[n / 2]) / 2.0;
    }

    return values[n / 2];
}


void printResult(
    const char* name,
    std::size_t orders,
    double seconds
) {
    const double ordersPerSecond =
        static_cast<double>(orders) / seconds;

    const double nanosecondsPerOrder =
        seconds * 1'000'000'000.0 /
        static_cast<double>(orders);

    std::cout << name << '\n';
    std::cout << "  Orders:       " << orders << '\n';
    std::cout << "  Time:         "
              << std::fixed << std::setprecision(6)
              << seconds << " s\n";
    std::cout << "  Orders/sec:   "
              << std::setprecision(2)
              << ordersPerSecond << '\n';
    std::cout << "  ns/order:     "
              << nanosecondsPerOrder << "\n\n";
}


/*
 * Benchmark resting-order insertion.

 * Price levels controls how many distinct BST nodes exist.
 *
 * Example:
 *   10 price levels
 *   100
 *   1,000
 *   10,000
 */
double benchmarkInsertion(
    std::size_t numberOfOrders,
    int priceLevels
) {
    Fixture f;

    const int basePrice = 1000;

    const auto start = std::chrono::steady_clock::now();

    for (std::size_t i = 0; i < numberOfOrders; ++i) {
        const int price =
            basePrice +
            static_cast<int>(i % priceLevels);

        Order* order = new Order(
            price,
            1,
            0,
            1,
            f.algo.getNextID(),
            true
        );

        f.book.placeBuyOrder(order);
    }

    const auto end = std::chrono::steady_clock::now();

    return std::chrono::duration<double>(
        end - start
    ).count();
}


/*
 * Benchmark actual matching.

 * We keep replenishing the ask side so that every
 * market order has liquidity to execute against.
 */
double benchmarkMatching(
    std::size_t numberOfOrders,
    int priceLevels
) {
    Fixture f;

    const int basePrice = 1000;
    constexpr int quantityPerOrder = 100;

    auto addLiquidity = [&]() {
        for (int level = 0; level < priceLevels; ++level) {
            Order* order = new Order(
                basePrice + level,
                quantityPerOrder,
                0,
                1 + (level % 99),
                f.algo.getNextID(),
                false
            );

            f.book.placeSellOrder(order);
        }
    };

    addLiquidity();

    const auto start = std::chrono::steady_clock::now();

    for (std::size_t i = 0; i < numberOfOrders; ++i) {
        if (f.book.getBestAsk() < 0) {
            addLiquidity();
        }

        const int traderID =
            1 + static_cast<int>(i % 99);

        f.book.placeBuyInstantOrder(1, traderID);
    }

    const auto end = std::chrono::steady_clock::now();

    return std::chrono::duration<double>(
        end - start
    ).count();
}


void runInsertionBenchmark(
    std::size_t orders,
    int priceLevels,
    int repetitions
) {
    std::vector<double> times;
    times.reserve(repetitions);

    for (int i = 0; i < repetitions; ++i) {
        times.push_back(
            benchmarkInsertion(orders, priceLevels)
        );
    }

    const double medianTime = median(times);

    std::cout << "\n========================================\n";
    std::cout << "RESTING ORDER INSERTION\n";
    std::cout << "Orders:       " << orders << '\n';
    std::cout << "Price levels: " << priceLevels << '\n';
    std::cout << "Runs:         " << repetitions << "\n\n";

    printResult(
        "Median result",
        orders,
        medianTime
    );
}


void runMatchingBenchmark(
    std::size_t orders,
    int priceLevels,
    int repetitions
) {
    std::vector<double> times;
    times.reserve(repetitions);

    for (int i = 0; i < repetitions; ++i) {
        times.push_back(
            benchmarkMatching(orders, priceLevels)
        );
    }

    const double medianTime = median(times);

    std::cout << "\n========================================\n";
    std::cout << "MARKET ORDER MATCHING\n";
    std::cout << "Orders:       " << orders << '\n';
    std::cout << "Price levels: " << priceLevels << '\n';
    std::cout << "Runs:         " << repetitions << "\n\n";

    printResult(
        "Median result",
        orders,
        medianTime
    );
}


int main() {
    constexpr int repetitions = 5;

    const std::vector<std::size_t> orderCounts = {
        10'000,
        100'000,
        1'000'000,
        10'000'000
    };

    const std::vector<int> priceLevels = {
        10,
        100,
        1'000,
        10'000,
        100'000
    };


    std::cout << "========================================\n";
    std::cout << "C++ LIMIT ORDER BOOK BENCHMARK\n";
    std::cout << "Median of " << repetitions << " runs\n";
    std::cout << "========================================\n";


    // --------------------------------------------------
    // 1. Scaling with number of orders
    // --------------------------------------------------

    std::cout << "\n\n";
    std::cout << "########################################\n";
    std::cout << "# SCALING WITH NUMBER OF ORDERS\n";
    std::cout << "########################################\n";

    // Keep this fixed while changing the number of orders.
    constexpr int fixedPriceLevels = 100;

    for (std::size_t orders : orderCounts) {
        runInsertionBenchmark(
            orders,
            fixedPriceLevels,
            repetitions
        );
    }

    for (std::size_t orders : orderCounts) {
        runMatchingBenchmark(
            orders,
            fixedPriceLevels,
            repetitions
        );
    }


    // --------------------------------------------------
    // 2. Scaling with number of price levels
    // --------------------------------------------------

    std::cout << "\n\n";
    std::cout << "########################################\n";
    std::cout << "# SCALING WITH NUMBER OF PRICE LEVELS\n";
    std::cout << "########################################\n";

    constexpr std::size_t fixedOrders = 1'000'000;

    for (int levels : priceLevels) {
        runInsertionBenchmark(
            fixedOrders,
            levels,
            repetitions
        );
    }

    for (int levels : priceLevels) {
        runMatchingBenchmark(
            fixedOrders,
            levels,
            repetitions
        );
    }


    return 0;
}