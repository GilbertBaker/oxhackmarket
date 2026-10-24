//
// Created by Gilbe on 17/08/2026.
//

#ifndef ORDERBOOK_ORDERBOOK_H
#define ORDERBOOK_ORDERBOOK_H
#include "OrderTree.h"
#include <iostream>
#include <unordered_map>
//#include "../Traders/TraderAlgo.h"
class TraderAlgo;

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

        //allows for it to be updated later
        TraderAlgo* algo;
    private:
        OrderTree* buyOrders;
        OrderTree* sellOrders;
        std::unordered_map<int, OrdListNode*> orders;
};


#endif //ORDERBOOK_ORDERBOOK_H
