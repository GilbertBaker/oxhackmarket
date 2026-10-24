//
// Created by Gilbe on 09/08/2026.
//

#include "Order.h"

Order::Order(int p, int q, int t, int traderID, int orderid, bool buyOrd) : price(p), quantity(q), time(t), id(traderID), orderID(orderid), buyOrder(buyOrd) {}