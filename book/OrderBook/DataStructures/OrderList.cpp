//
// Created by Gilbe on 11/08/2026.
//

#include "OrderList.h"

#include "OrdListNode.h"

OrderList::OrderList(int p) : price(p) {
    head=nullptr;
    tail=nullptr;
}

//Removes first order from list and returns it.
Order* OrderList::PopOrder() {
    OrdListNode* temp = head;
    if (temp != nullptr) {
        //set tail to null if list is now empty
        if (temp==tail) {
            tail = nullptr;
        }
        head = temp->next;
        if (head!=nullptr) {
            head->prev = nullptr;
        }
        Order* ord = temp->order;

        //delete for mem
        delete temp;
        return ord;
    }
    return nullptr;
}

//returns the first order
Order* OrderList::getFirstOrder() {
    if (head==nullptr) {
        return nullptr;
    }
    return head->order;
}

//delete the first order
void OrderList::deleteFirstOrder() {
    if (head==nullptr) {
        return;
    }
    OrdListNode* temp = head;
    head = head->next;
    if (head!=nullptr) {
        head->prev = nullptr;
    }
    if (tail==temp) {tail=nullptr;}
    delete temp;
}

//add an order to the back of the list
OrdListNode* OrderList::PushOrder(Order* ord) {
    if (head==nullptr) {
        OrdListNode* ordNode = new OrdListNode(ord, this);
        head = ordNode;
        tail = ordNode;
        return ordNode;
    }
    else {
        tail->next = new OrdListNode(ord, this );
        tail->next->prev = tail;
        tail = tail->next;
        return tail;
    }

}

int OrderList::CalcOrders() {
    if (head==nullptr) {
        return 0;
    }
    OrdListNode* temp = head;
    int tot = 0;
    while (temp!=nullptr) {
        tot += temp->order->quantity;
        temp = temp->next;
    }
    return tot;
}


