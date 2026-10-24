//
// Created by Gilbe on 10/08/2026.
//

#ifndef ORDERBOOK_ORDERTREE_H
#define ORDERBOOK_ORDERTREE_H
#include "OrderList.h"
#include "../Order.h"


class OrderTree {
    public:
    //OrderTree(Order* ord); depreciated
    OrderTree();
    OrderTree* addOrder(Order* ord, OrdListNode*& insertedNode);
    OrderTree* left;
    OrderTree* right;
    OrderList* ordList;
    Order* getHighestOrder();
    Order* getLowestOrder();
    int height;
    //bool deleteLowestOrder();
    //bool deleteHighestOrder();
    void printLowestLevels(int& printed, int maxLevels);
    void printHighestLevels(int& printed, int maxLevels);

    int getHeight(OrderTree* node);
    int getBalance();
    void updateHeight();
    OrderTree* rotateRight();
    OrderTree* rotateLeft();

    //delete price levels
    OrderTree* removePrice(int price);
    OrderTree* detachMin(OrderTree*& minNode);
    OrderTree* rebalance();
};



#endif //ORDERBOOK_ORDERTREE_H
