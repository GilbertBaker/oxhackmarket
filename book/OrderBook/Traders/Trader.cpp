//
// Created by Gilbe on 17/08/2026.
//

#include "Trader.h"
#include "TraderAlgo.h"
#include "../DataStructures/OrderBook.h"
#include "../Order.h"
#include <random>
Trader::Trader(int id) : ID(id) {
    money= 50000;
    qOwned = 0;
    reservedMoney= 0;
    reservedQuantity=0;
}

//current strategy:
//1% ish for each market buy/sell and limit buy/sell.
//always put 1000 on the order.
//
void Trader::Update() {
    //TODO: make this more complicated.
    // Random integer from 1 to 10 inclusive
    std::uniform_int_distribution<int> dist(0, 101);

    int v = dist(algo->rng);
    if (v<=49) {
        if (v<=25) {
            //market buy
            marketBuy();
        }
        else {
            //limit buy
            limitBuy();
        }
    }

    else {
        if (v<=74) {
            //market sell
            marketSell();
        }
        else if (v <=99) {
            //limit sell
            limitSell();
        }
        else {
            //low chance but to stop it freezing: cancel
            if (currentOrders.size() > 0) {
                algo->orderBook->cancelOrder(currentOrders[0]);
            }
        }
    }

}

void Trader::marketBuy() {
    int bestAsk = algo->orderBook->getBestAsk();

    if (bestAsk <= 0) {
        return;
    }

    int max = (money - reservedMoney) / bestAsk;
    if (max<=1) {return;}
    std::uniform_int_distribution<int> dist2(1, max);
    int q = dist2(algo->rng);

    algo->orderBook->placeBuyInstantOrder(q,ID);
}
void Trader::limitBuy() {
    OrderBook* ordBook = algo -> orderBook;
    int buyPrice = ordBook->getBestAsk();
    int sellPrice = ordBook->getBestBid();
    if (buyPrice<=0) {buyPrice=sellPrice+20; if (sellPrice <=0) {buyPrice=1000; sellPrice=1000;}}
    int dif = buyPrice-sellPrice;

    int minPrice = std::max(1, sellPrice-50);
    // Random integer from 1 to 10 inclusive
    std::uniform_int_distribution<int> dist(minPrice,buyPrice+50);

    int l = dist(algo->rng);

    //work out quantity
    int maxAmount = (money-reservedMoney)/l;
    if (maxAmount<=1) {return;}
    std::uniform_int_distribution<int> dist2(1, maxAmount);
    int q = dist2(algo->rng);


    //if no orders default to 1000
    if (buyPrice<=0) {buyPrice=ordBook->getBestBid(); if (buyPrice<=0) {buyPrice=1000;}}
    if (l*q>money-reservedMoney) {return;}
    Order* order = new Order(l,q,algo->getCurrentTime(),ID,algo->getNextID(), true);
    ordBook->placeBuyOrder(order);
}
void Trader::limitSell() {
    if (qOwned<=0) {return;}
    OrderBook* ordBook = algo -> orderBook;
    int buyPrice = ordBook->getBestAsk();
    int sellPrice = ordBook->getBestBid();
    if (buyPrice<=0) {buyPrice=sellPrice+20; if (sellPrice <=0) {buyPrice=1000; sellPrice=1000;}}

    // Random integer from 1 to 10 inclusive
    int minPrice = std::max(1, sellPrice-50);

    std::uniform_int_distribution<int> dist(minPrice, buyPrice+50);

    int l = dist(algo->rng);

    //work out quantity
    int maxQ = qOwned-reservedQuantity;
    if (maxQ<=1) {return;}
    std::uniform_int_distribution<int> dist2(1, maxQ);
    int q = dist2(algo->rng);


    //if no orders default to 1000
    if (buyPrice<=0) {buyPrice=ordBook->getBestAsk(); if (buyPrice<=0) {buyPrice=1000;}}
    if (q>qOwned-reservedQuantity) {return;}
    Order* order = new Order(l,q,algo->getCurrentTime(),ID, algo->getNextID(), false);
    ordBook->placeSellOrder(order);

}
void Trader::marketSell() {

    //work out quantity
    if (qOwned-reservedQuantity<=1) {return;}
    std::uniform_int_distribution<int> dist2(1, qOwned-reservedQuantity);
    int q = dist2(algo->rng);

    //place
    algo->orderBook->placeSellInstantOrder(q,ID);
}