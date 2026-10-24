//
// Created by Gilbe on 17/08/2026.
//
#include <chrono>
#include <algorithm>
#include "OrderBook.h"

#include "OrdListNode.h"
#include "../Traders/TraderAlgo.h"
#include "../Traders/Trader.h"
using namespace std::chrono;
void OrderBook::placeBuyOrder(Order* order) {
    int q = order->quantity;

    Trader* buyer = algo->getTrader(order->id);

    while (q > 0) {
        Order* matchedOrd = sellOrders->getLowestOrder();

        // No sellers left
        if (matchedOrd == nullptr) {
            break;
        }

        // Cheapest seller is too expensive
        if (matchedOrd->price > order->price) {
            break;
        }

        int p = matchedOrd->price;
        int fillQuantity = std::min(q, matchedOrd->quantity);

        Trader* seller = algo->getTrader(matchedOrd->id);

        // Transfer money
        buyer->money -= fillQuantity * p;
        seller->money += fillQuantity * p;

        // Transfer shares
        seller->qOwned -= fillQuantity;
        buyer->qOwned += fillQuantity;

        q -= fillQuantity;
        matchedOrd->quantity -= fillQuantity;
        seller -> reservedQuantity -= fillQuantity;
        if (matchedOrd->quantity == 0) {
            deleteOrder(matchedOrd->orderID);
        }
    }

    // Whatever wasn't immediately filled becomes a resting limit order
    if (q > 0) {
        order->quantity = q;
        OrdListNode* node = nullptr;

        buyOrders = buyOrders->addOrder(order, node);

        orders[order->orderID] = node;

        buyer->currentOrders.push_back(order->orderID);
        buyer -> reservedMoney += q*order->price;
    }
    else {
        // The entire incoming order was filled, so it isn't stored anywhere
        delete order;
    }
}
void OrderBook::placeSellOrder(Order* order) {
    int q = order->quantity;

    Trader* seller = algo->getTrader(order->id);

    while (q > 0) {
        Order* matchedOrd = buyOrders->getHighestOrder();

        // No buyers left
        if (matchedOrd == nullptr) {
            break;
        }

        // Highest buyer isn't offering enough
        if (matchedOrd->price < order->price) {
            break;
        }

        int p = matchedOrd->price;
        int fillQuantity = std::min(q, matchedOrd->quantity);

        Trader* buyer = algo->getTrader(matchedOrd->id);

        // Transfer money
        buyer->money -= fillQuantity * p;
        seller->money += fillQuantity * p;

        // Transfer shares
        seller->qOwned -= fillQuantity;
        buyer->qOwned += fillQuantity;


        q -= fillQuantity;
        matchedOrd->quantity -= fillQuantity;
        buyer -> reservedMoney -= fillQuantity*p;

        if (matchedOrd->quantity == 0) {
            deleteOrder(matchedOrd);
        }
    }

    // Unfilled portion stays as a limit order
    if (q > 0) {
        order->quantity = q;
        seller->currentOrders.push_back(order->orderID);

        OrdListNode* node = nullptr;

        sellOrders = sellOrders->addOrder(order, node);

        orders[order->orderID] = node;

        seller -> reservedQuantity += q;
    }
    else {
        delete order;
    }
}

void OrderBook::placeBuyInstantOrder(int quantity, int traderID) {
    int q = quantity;

    while (q > 0) {
        Order* matchedOrd = sellOrders->getLowestOrder();

        if (matchedOrd == nullptr) {
            return;
        }

        int p = matchedOrd->price;

        Trader* seller = algo->getTrader(matchedOrd->id);
        Trader* buyer = algo->getTrader(traderID);

        int fillQuantity = std::min(q, matchedOrd->quantity);

        // Transfer money
        buyer->money -= fillQuantity * p;
        seller->money += fillQuantity * p;

        // Transfer shares
        seller->qOwned -= fillQuantity;
        buyer->qOwned += fillQuantity;

        // Reduce both orders
        q -= fillQuantity;
        matchedOrd->quantity -= fillQuantity;
        seller -> reservedQuantity -= fillQuantity;

        // Remove completely filled order
        if (matchedOrd->quantity == 0) {
            deleteOrder(matchedOrd->orderID);
        }
    }
}
void OrderBook::placeSellInstantOrder(int quantity, int traderID) {
    int q = quantity;

    while (q > 0) {
        Order* matchedOrd = buyOrders->getHighestOrder();

        // No buyers left
        if (matchedOrd == nullptr) {
            return;
        }

        int p = matchedOrd->price;

        Trader* buyer = algo->getTrader(matchedOrd->id);
        Trader* seller = algo->getTrader(traderID);

        int fillQuantity = std::min(q, matchedOrd->quantity);

        // Transfer money
        buyer->money -= fillQuantity * p;
        seller->money += fillQuantity * p;

        // Transfer shares
        seller->qOwned -= fillQuantity;
        buyer->qOwned += fillQuantity;

        // Reduce both orders
        q -= fillQuantity;
        matchedOrd->quantity -= fillQuantity;
        buyer->reservedMoney -= fillQuantity*p;
        // Remove completely filled order
        if (matchedOrd->quantity == 0) {
            deleteOrder(matchedOrd->orderID);
        }
    }
}


//for simplicity
bool OrderBook::deleteOrder(Order* matchedOrd) {
    int id = matchedOrd->orderID;

    return deleteOrder(id);
}

bool OrderBook::deleteOrder(int id) {
    auto it = orders.find(id);

    if (it == orders.end())
        return false;

    ////////////
    //for deleting node from tree if necessary, keep track of this
    OrderList* list = it->second->parentList;
    int price = list->price;
    bool isBuyOrder = it->second->order->buyOrder;
    /////////////

    std::erase(algo->getTrader(it->second->order->id)->currentOrders, id);
    delete it->second->order;
    delete it->second;
    orders.erase(it);

    //////////////////
    //back to removing empty list from tree
    if (list->head == nullptr) {
        if (isBuyOrder) {
            buyOrders = buyOrders->removePrice(price);

            if (buyOrders == nullptr) {
                buyOrders = new OrderTree();
            }
        }
        else {
            sellOrders = sellOrders->removePrice(price);

            if (sellOrders == nullptr) {
                sellOrders = new OrderTree();
            }
        }
    }

    return true;
}

int OrderBook::getBestAsk() {
    Order* out = sellOrders->getLowestOrder();
    if (out==nullptr) {return -1;}
    return out->price;

}
int OrderBook::getBestBid() {
    Order* out = buyOrders->getHighestOrder();
    if (out==nullptr) {return -1;}
    return out->price;
}
int OrderBook::getMeanPrice() {
    int ask = getBestAsk();
    int bid = getBestBid();

    if (ask < 0 && bid < 0)
        return -1;

    if (ask < 0)
        return bid;

    if (bid < 0)
        return ask;

    return (ask + bid) / 2;
}

//TODO: make this a better method
OrderBook::OrderBook() {
    buyOrders = new OrderTree();
    sellOrders = new OrderTree();
    algo = nullptr;
    orders = std::unordered_map<int, OrdListNode*>();
}

void OrderBook::outputOrderBook() {
    std::cout << "\nSELLS:\n";

    int count = 0;
    sellOrders->printLowestLevels(count, 3);

    std::cout << "BUYS:\n";

    count = 0;
    buyOrders->printHighestLevels(count, 3);

    std::cout << '\n';
}

void OrderBook::cancelOrder(int orderID) {
    //delete order returns false if any errors, so this works
    auto it = orders.find(orderID);

    if (it == orders.end()) {
        return;
    }

    OrdListNode* ordNode = it->second;
    if (ordNode == nullptr) {return;}
    Order* order = ordNode->order;
    if (order == nullptr) {return;}

    Trader* trader = algo->getTrader(order->id);
    if (order->buyOrder) {
        trader->reservedMoney -= order->price*order->quantity;
    }
    else {
        trader->reservedQuantity -= order->quantity;
    }


    if (!deleteOrder(orderID)) {
        std::cout << "\nInvalid cancel ID error\n";
    }
}