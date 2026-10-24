//
// Created by Gilbe on 17/08/2026.
//

#include <vector>
#ifndef ORDERBOOK_TRADER_H
#define ORDERBOOK_TRADER_H
class TraderAlgo;


class Trader {
    public:
    Trader(int id);
    void Update();
    const int ID;
    int qOwned;
    int money;
    int reservedQuantity;
    int reservedMoney;
    TraderAlgo* algo;
    void limitSell();
    void limitBuy();
    void marketSell();
    void marketBuy();

    std::vector<int> currentOrders;

};


#endif //ORDERBOOK_TRADER_H
