//
// Created by Gilbe on 17/08/2026.
//

#include "TraderAlgo.h"
#include "Trader.h"
#include <chrono>
#include <memory>
#include <unordered_map>




TraderAlgo::TraderAlgo(OrderBook* ordBook, int noOfTraders) : orderBook(ordBook), rng(std::random_device{}()), noOfTraders(noOfTraders) {
    currentTradeID =0;
    curID=0;
    //TODO: do something about 0, it needs to be set to something that isnt null but isnt a trader
    for (int i=curID;i<noOfTraders;i++) {
        traders[i] = new Trader(i);
        traders[i] ->algo = this;
        traders[i] -> qOwned = 1000;
        curID+=1;
    }
}

int TraderAlgo::getNextID() {
    int id = currentTradeID;
    currentTradeID+=1;
    return id;
}

void TraderAlgo::Update() {
    for (int i=1;i<noOfTraders;i++) {
        traders[i]->Update();
    }
}

Trader* TraderAlgo::getTrader(int id) {
    //traders that weren't created up front (e.g. web users) are created on first use
    auto it = traders.find(id);
    if (it != traders.end() && it->second != nullptr) {
        return it->second;
    }
    Trader* trader = new Trader(id);
    trader->algo = this;
    traders[id] = trader;
    return trader;
}

int TraderAlgo::getCurrentTime() {
    return (int) duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}