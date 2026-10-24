//
// Created by Gilbe on 10/08/2026.
//

#ifndef ORDERBOOK_ORDLIST_H
#define ORDERBOOK_ORDLIST_H
#include "../Order.h"

class OrderList;

class OrdListNode {
    public:
    OrdListNode(Order* ord, OrderList* parentListObject);
    ~OrdListNode();
    OrdListNode* next;
    OrdListNode* prev;
    OrderList* parentList;
    Order* order;
    
};


#endif //ORDERBOOK_ORDLIST_H
