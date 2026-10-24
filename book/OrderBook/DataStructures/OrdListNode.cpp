//
// Created by Gilbe on 10/08/2026.
//

#include "OrdListNode.h"

#include "OrderList.h"


OrdListNode::OrdListNode(Order* ord, OrderList* parentListObject) {
    order = ord;
    next = nullptr;
    prev = nullptr;
    parentList = parentListObject;

}


OrdListNode::~OrdListNode() {
    if (next != nullptr) {
        next->prev = prev;
    }
    else if (prev != nullptr) {
        parentList->tail = prev;
    }
    else {
        parentList->head = nullptr;
        parentList->tail = nullptr;
    }
    if (prev != nullptr) {
        prev->next = next;
    }
    else if (next != nullptr) {
        parentList->head = next;
    }
    else {
        parentList->head = nullptr;
        parentList->tail = nullptr;
    }

}
