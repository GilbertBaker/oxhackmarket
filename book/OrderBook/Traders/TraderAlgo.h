//
// Created by Gilbe on 17/08/2026.
//

#ifndef ORDERBOOK_TRADERALGO_H
#define ORDERBOOK_TRADERALGO_H
#include <random>
#include <unordered_map>

//#include "Trader.h"
//#include "../DataStructures/OrderBook.h"
class Trader;
class OrderBook;

class TraderAlgo {
    public:
    TraderAlgo(OrderBook* ordBook);
    void Update();
    Trader* getTrader(int id);
    std::unordered_map<int, Trader*> traders;
    OrderBook* orderBook;
    int getCurrentTime();
    int getNextID();
    std::mt19937 rng;

    private:
    int curID;
    int currentTradeID;
    int noOfTraders;
};



#endif //ORDERBOOK_TRADERALGO_H
