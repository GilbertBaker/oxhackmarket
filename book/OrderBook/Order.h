//
// Created by Gilbe on 09/08/2026.
//

#ifndef ORDERBOOK_ORDER_H
#define ORDERBOOK_ORDER_H


class Order {
public:
    Order(int p, int q, int t, int traderID, int orderid, bool buyOrd);
    const int price;
    const int time;
    const int id;
    int quantity;
    int orderID;
    bool buyOrder; // true for buy, false for sell
};


#endif //ORDERBOOK_ORDER_H
