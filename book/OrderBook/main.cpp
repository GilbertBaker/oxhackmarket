#include <iostream>
#include <string>

#include "Traders/TraderAlgo.h"
#include "DataStructures/OrderBook.h"

int main() {
    std::cout << "Hello, World!" << std::endl;
    int count =0;
    OrderBook orderBook = OrderBook();
    TraderAlgo tAlgo =  TraderAlgo(&orderBook);
    orderBook.algo=&tAlgo;
    while (true) {
        tAlgo.Update();
        std::cout << tAlgo.orderBook->getMeanPrice() << '\n';
        orderBook.outputOrderBook();
        count++;
        if (count > 100000) {
            return 0;
        }
    }
    return 0;
}

