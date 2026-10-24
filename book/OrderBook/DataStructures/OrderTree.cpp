//
// Created by Gilbe on 10/08/2026.
//

#include "OrderTree.h"
#include <iostream>

OrderTree::OrderTree() {
    ordList = nullptr;
    left = nullptr;
    right = nullptr;
}

OrderTree* OrderTree::addOrder(Order* ord, OrdListNode*& insertedNode) {
    // Empty tree node: initialise its price level and insert the order.
    if (ordList == nullptr) {
        ordList = new OrderList(ord->price);
        insertedNode = ordList->PushOrder(ord);
        height = 1;
        return this;
    }

    int p = ord->price;

    // Insert into left subtree.
    if (p < ordList->price) {
        if (left == nullptr) {
            left = new OrderTree();
        }

        left = left->addOrder(ord, insertedNode);
    }

    // Insert into right subtree.
    else if (p > ordList->price) {
        if (right == nullptr) {
            right = new OrderTree();
        }

        right = right->addOrder(ord, insertedNode);
    }

    // Same price: append to this price level's FIFO list.
    else {
        insertedNode = ordList->PushOrder(ord);
        return this;
    }

    // Update height after insertion into a subtree.
    height = 1 + std::max(
        getHeight(left),
        getHeight(right)
    );

    int balance = getHeight(left) - getHeight(right);

    // LL case.
    if (balance > 1 && p < left->ordList->price) {
        return rotateRight();
    }

    // RR case.
    if (balance < -1 && p > right->ordList->price) {
        return rotateLeft();
    }

    // LR case.
    if (balance > 1 && p > left->ordList->price) {
        left = left->rotateLeft();
        return rotateRight();
    }

    // RL case.
    if (balance < -1 && p < right->ordList->price) {
        right = right->rotateRight();
        return rotateLeft();
    }

    return this;
}

Order* OrderTree::getLowestOrder() {
    if (left != nullptr) {
        Order* order = left->getLowestOrder();

        if (order != nullptr) {
            return order;
        }
    }

    Order* currentOrder = nullptr;
    if (ordList != nullptr) {
        currentOrder = ordList->getFirstOrder();
    }

    if (currentOrder != nullptr) {
        return currentOrder;
    }

    if (right != nullptr) {
        return right->getLowestOrder();
    }

    return nullptr;
}

Order* OrderTree::getHighestOrder() {
    if (right != nullptr) {
        Order* order = right->getHighestOrder();

        if (order != nullptr) {
            return order;
        }
    }

    Order* currentOrder = nullptr;
    if (ordList != nullptr) {
        currentOrder = ordList->getFirstOrder();
    }

    if (currentOrder != nullptr) {
        return currentOrder;
    }

    if (left != nullptr) {
        return left->getHighestOrder();
    }

    return nullptr;
}

/*bool OrderTree::deleteLowestOrder() {
    if (left != nullptr) {
        if (left->deleteLowestOrder()) {
            return true;
        }
    }

    if (ordList != nullptr && ordList->getFirstOrder() != nullptr) {
        delete ordList->PopOrder();
        return true;
    }

    if (right != nullptr) {
        return right->deleteLowestOrder();
    }

    return false;
}*/

/*bool OrderTree::deleteHighestOrder() {
    if (right != nullptr) {
        if (right->deleteHighestOrder()) {
            return true;
        }
    }

    if (ordList != nullptr && ordList->getFirstOrder() != nullptr) {
        delete ordList->PopOrder();
        return true;
    }

    if (left != nullptr) {
        return left->deleteHighestOrder();
    }

    return false;
}*/

void OrderTree::printLowestLevels(int& printed, int maxLevels) {
    if (printed >= maxLevels) return;

    if (left != nullptr)
        left->printLowestLevels(printed, maxLevels);

    if (printed >= maxLevels) return;

    if (ordList !=nullptr) {
        int quantity = ordList->CalcOrders();

        if (quantity > 0) {
            std::cout << ordList->price << " x " << quantity << '\n';
            printed++;
        }
    }

    if (right != nullptr)
        right->printLowestLevels(printed, maxLevels);
}

void OrderTree::printHighestLevels(int& printed, int maxLevels) {
    if (printed >= maxLevels) return;

    if (right != nullptr)
        right->printHighestLevels(printed, maxLevels);

    if (printed >= maxLevels) return;

    if (ordList !=nullptr) {
        int quantity = ordList->CalcOrders();

        if (quantity > 0) {
            std::cout << ordList->price << " x " << quantity << '\n';
            printed++;
        }
    }

    if (left != nullptr)
        left->printHighestLevels(printed, maxLevels);
}


int OrderTree::getHeight(OrderTree* node) {
    if (node == nullptr)
        return 0;

    return node->height;
}

int OrderTree::getBalance() {
    return getHeight(left) - getHeight(right);
}

void OrderTree::updateHeight() {
    height = 1 + std::max(
        getHeight(left),
        getHeight(right)
    );
}

OrderTree* OrderTree::rotateRight() {
    OrderTree* x = left;
    OrderTree* B = x->right;

    x->right = this;
    left = B;

    updateHeight();
    x->updateHeight();

    return x;
}
OrderTree* OrderTree::rotateLeft() {
    OrderTree* y = right;
    OrderTree* B = y->left;

    y->left = this;
    right = B;

    updateHeight();
    y->updateHeight();

    return y;
}


//removing nodes:

OrderTree* OrderTree::rebalance() {
    updateHeight();

    int balance = getBalance();

    // Left-heavy
    if (balance > 1) {
        if (left->getBalance() < 0) {
            left = left->rotateLeft();
        }

        return rotateRight();
    }

    // Right-heavy
    if (balance < -1) {
        if (right->getBalance() > 0) {
            right = right->rotateRight();
        }

        return rotateLeft();
    }

    return this;
}


OrderTree* OrderTree::detachMin(OrderTree*& minNode) {
    // This is the smallest node.
    if (left == nullptr) {
        minNode = this;

        OrderTree* newRoot = right;

        // Detach this node completely.
        left = nullptr;
        right = nullptr;

        return newRoot;
    }

    left = left->detachMin(minNode);

    return rebalance();
}

OrderTree* OrderTree::removePrice(int price) {
    if (ordList == nullptr) {
        return this;
    }

    // Find the relevant price level.
    if (price < ordList->price) {
        if (left != nullptr) {
            left = left->removePrice(price);
        }
    }

    else if (price > ordList->price) {
        if (right != nullptr) {
            right = right->removePrice(price);
        }
    }

    else {
        // We found the price level.

        // You should only remove a price level once its FIFO list is empty.
        if (ordList->head != nullptr) {
            return this;
        }

        // ----------------------------
        // Case 1: no children
        // ----------------------------
        if (left == nullptr && right == nullptr) {
            delete ordList;
            ordList = nullptr;

            delete this;

            return nullptr;
        }

        // ----------------------------
        // Case 2: only right child
        // ----------------------------
        if (left == nullptr) {
            OrderTree* child = right;

            delete ordList;

            // Prevent this node from retaining tree links.
            right = nullptr;

            delete this;

            return child;
        }

        // ----------------------------
        // Case 3: only left child
        // ----------------------------
        if (right == nullptr) {
            OrderTree* child = left;

            delete ordList;

            left = nullptr;

            delete this;

            return child;
        }

        // ----------------------------
        // Case 4: two children
        // ----------------------------

        OrderTree* successor = nullptr;

        // Remove smallest node from right subtree.
        right = right->detachMin(successor);

        // Current ordList is empty and no longer needed.
        delete ordList;

        // Successor takes this node's position.
        successor->left = left;
        successor->right = right;

        // Detach before deleting this.
        left = nullptr;
        right = nullptr;
        ordList = nullptr;

        delete this;

        return successor->rebalance();
    }

    return rebalance();
}