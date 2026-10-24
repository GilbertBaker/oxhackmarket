//
// MarketServer: runs one OrderBook per market and speaks a line protocol on
// stdin/stdout (see plan/COLLAB.txt section 8). Used by server/engine.py.
//

#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "DataStructures/OrderBook.h"
#include "Traders/TraderAlgo.h"
#include "Order.h"

struct Market {
    OrderBook book;
    TraderAlgo algo{&book, 0};

    Market() {
        book.algo = &algo;
    }
};

//unique_ptr so a Market never moves when the map rehashes (book.algo points into it)
std::unordered_map<int, std::unique_ptr<Market>> markets;

void end() {
    std::cout << "END" << std::endl;
}

void err(const std::string& reason) {
    std::cout << "ERR " << reason << '\n';
    end();
}

void printFills(OrderBook& book) {
    for (const Fill& f : book.fills) {
        std::cout << "FILL " << f.makerOrderID << ' ' << f.makerTraderID << ' ' << f.price << ' ' << f.qty << '\n';
    }
}

//reads exactly n ints from the stream and nothing more
bool readInts(std::istringstream& in, std::vector<long long>& out, int n) {
    for (int i = 0; i < n; i++) {
        long long v;
        if (!(in >> v)) return false;
        out.push_back(v);
    }
    std::string extra;
    return !(in >> extra);
}

Market* findMarket(long long id) {
    auto it = markets.find((int) id);
    if (it == markets.end()) return nullptr;
    return it->second.get();
}

void handle(const std::string& line) {
    std::istringstream in(line);
    std::string cmd;
    if (!(in >> cmd)) {
        err("bad_command");
        return;
    }

    if (cmd == "PING") {
        std::cout << "PONG\n";
        end();
    }
    else if (cmd == "NEW") {
        std::vector<long long> a;
        if (!readInts(in, a, 1)) { err("bad_command"); return; }
        if (findMarket(a[0]) == nullptr) {
            markets[(int) a[0]] = std::make_unique<Market>();
        }
        std::cout << "OK\n";
        end();
    }
    else if (cmd == "LIMIT") {
        //LIMIT <mkt> <orderId> <traderId> <B|S> <price> <qty>
        long long mkt, orderId, traderId, price, qty;
        std::string side, extra;
        if (!(in >> mkt >> orderId >> traderId >> side >> price >> qty) || (in >> extra) || (side != "B" && side != "S")) {
            err("bad_command");
            return;
        }
        Market* m = findMarket(mkt);
        if (m == nullptr) { err("unknown_market"); return; }
        if (price < 1 || price > 99 || qty <= 0 || qty > 1000000000) { err("bad_args"); return; }
        if (m->book.getRestingQty((int) orderId) > 0) { err("duplicate_id"); return; }

        m->book.fills.clear();
        Order* order = new Order((int) price, (int) qty, 0, (int) traderId, (int) orderId, side == "B");
        if (side == "B") m->book.placeBuyOrder(order);
        else m->book.placeSellOrder(order);

        printFills(m->book);
        std::cout << "RESTING " << m->book.getRestingQty((int) orderId) << '\n';
        m->book.fills.clear();
        end();
    }
    else if (cmd == "MARKET") {
        //MARKET <mkt> <traderId> <B|S> <qty>
        long long mkt, traderId, qty;
        std::string side, extra;
        if (!(in >> mkt >> traderId >> side >> qty) || (in >> extra) || (side != "B" && side != "S")) {
            err("bad_command");
            return;
        }
        Market* m = findMarket(mkt);
        if (m == nullptr) { err("unknown_market"); return; }
        if (qty <= 0 || qty > 1000000000) { err("bad_args"); return; }

        m->book.fills.clear();
        if (side == "B") m->book.placeBuyInstantOrder((int) qty, (int) traderId);
        else m->book.placeSellInstantOrder((int) qty, (int) traderId);

        long long filled = 0;
        for (const Fill& f : m->book.fills) filled += f.qty;
        printFills(m->book);
        std::cout << "UNFILLED " << qty - filled << '\n';
        m->book.fills.clear();
        end();
    }
    else if (cmd == "CANCEL") {
        std::vector<long long> a;
        if (!readInts(in, a, 2)) { err("bad_command"); return; }
        Market* m = findMarket(a[0]);
        if (m == nullptr) { err("unknown_market"); return; }
        if (m->book.getRestingQty((int) a[1]) <= 0) { err("not_found"); return; }
        m->book.cancelOrder((int) a[1]);
        std::cout << "OK\n";
        end();
    }
    else if (cmd == "BOOK") {
        std::vector<long long> a;
        if (!readInts(in, a, 2)) { err("bad_command"); return; }
        Market* m = findMarket(a[0]);
        if (m == nullptr) { err("unknown_market"); return; }
        if (a[1] <= 0) { err("bad_args"); return; }
        for (auto& level : m->book.getDepth(true, (int) a[1])) {
            std::cout << "BID " << level.first << ' ' << level.second << '\n';
        }
        for (auto& level : m->book.getDepth(false, (int) a[1])) {
            std::cout << "ASK " << level.first << ' ' << level.second << '\n';
        }
        end();
    }
    else {
        err("bad_command");
    }
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cout << "READY" << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line == "QUIT") break;
        if (line.empty()) continue;
        handle(line);
    }
    return 0;
}
