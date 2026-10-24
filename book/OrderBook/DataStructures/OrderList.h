//
// Created by Gilbe on 11/08/2026.
//

#ifndef ORDERBOOK_ORDERLIST_H
#define ORDERBOOK_ORDERLIST_H
class OrdListNode;
#include "../Order.h"

class OrderList {
    public:
    OrderList(int p);
    Order* PopOrder();
    OrdListNode* PushOrder(Order* ord);
    void deleteFirstOrder();
    Order* getFirstOrder();
    const int price;
    int CalcOrders();

    OrdListNode* head;
    OrdListNode* tail;



};


#endif //ORDERBOOK_ORDERLIST_H
