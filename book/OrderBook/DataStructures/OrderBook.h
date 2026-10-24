//
// Created by Gilbe on 17/08/2026.
//

#ifndef ORDERBOOK_ORDERBOOK_H
#define ORDERBOOK_ORDERBOOK_H
#include "OrderTree.h"
#include <iostream>
#include <unordered_map>
#include <vector>
//#include "../Traders/TraderAlgo.h"
class TraderAlgo;

//one match between a resting (maker) order and an incoming (taker) order
struct Fill {
    int makerOrderID;
    int makerTraderID;
    int takerTraderID;
    int price;
    int qty;
    bool takerIsBuy;
};

class OrderBook {
    public:
        void placeBuyOrder(Order* order);
        void placeSellOrder(Order* order);

        void placeBuyInstantOrder(int quantity, int traderID);
        void placeSellInstantOrder(int quantity, int traderID);

        bool deleteOrder(Order* matchedOrd);
        bool deleteOrder(int orderID);


        int getBestAsk();
        int getBestBid();
        int getMeanPrice();
        OrderBook();
        void outputOrderBook();
        void cancelOrder(int orderID);

        //levels as (price, total quantity), best price first
        std::vector<std::pair<int,int>> getDepth(bool bids, int maxLevels);
        //quantity still resting for an order id, 0 if it isn't in the book
        int getRestingQty(int orderID) const;

        //appended by the matching loops, drained by the caller
        std::vector<Fill> fills;

        //allows for it to be updated later
        TraderAlgo* algo;
    private:
        OrderTree* buyOrders;
        OrderTree* sellOrders;
        std::unordered_map<int, OrdListNode*> orders;
};


#endif //ORDERBOOK_ORDERBOOK_H
