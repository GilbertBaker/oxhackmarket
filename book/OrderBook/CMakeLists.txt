cmake_minimum_required(VERSION 4.3)
project(OrderBook)

set(CMAKE_CXX_STANDARD 20)

add_executable(OrderBook main.cpp
        Order.cpp
        Order.h
        DataStructures/OrderTree.cpp
        DataStructures/OrderTree.h
        DataStructures/OrdListNode.cpp
        DataStructures/OrdListNode.h
        DataStructures/OrdListNode.h
        DataStructures/OrderList.cpp
        DataStructures/OrderList.h
        DataStructures/OrderBook.cpp
        DataStructures/OrderBook.h
        Traders/Trader.cpp
        Traders/Trader.h
        Traders/TraderAlgo.cpp
        Traders/TraderAlgo.h
)

add_executable(OrderBookTests
        OrderBookTests.cpp
        Order.cpp
        Order.h
        DataStructures/OrderTree.cpp
        DataStructures/OrderTree.h
        DataStructures/OrdListNode.cpp
        DataStructures/OrdListNode.h
        DataStructures/OrdListNode.h
        DataStructures/OrderList.cpp
        DataStructures/OrderList.h
        DataStructures/OrderBook.cpp
        DataStructures/OrderBook.h
        Traders/Trader.cpp
        Traders/Trader.h
        Traders/TraderAlgo.cpp
        Traders/TraderAlgo.h
)

add_executable(OrderBookBenchmark
        OrderBookBenchmark.cpp
        Order.cpp
        Order.h
        DataStructures/OrderTree.cpp
        DataStructures/OrderTree.h
        DataStructures/OrdListNode.cpp
        DataStructures/OrdListNode.h
        DataStructures/OrdListNode.h
        DataStructures/OrderList.cpp
        DataStructures/OrderList.h
        DataStructures/OrderBook.cpp
        DataStructures/OrderBook.h
        Traders/Trader.cpp
        Traders/Trader.h
        Traders/TraderAlgo.cpp
)

# Line-protocol matching engine used by the Flask server (server/engine.py)
add_executable(MarketServer
        MarketServer.cpp
        Order.cpp
        Order.h
        DataStructures/OrderTree.cpp
        DataStructures/OrderTree.h
        DataStructures/OrdListNode.cpp
        DataStructures/OrdListNode.h
        DataStructures/OrderList.cpp
        DataStructures/OrderList.h
        DataStructures/OrderBook.cpp
        DataStructures/OrderBook.h
        Traders/Trader.cpp
        Traders/Trader.h
        Traders/TraderAlgo.cpp
        Traders/TraderAlgo.h
)
# static runtime so the exe runs without mingw on PATH
if (MINGW)
    target_link_options(MarketServer PRIVATE -static-libgcc -static-libstdc++)
    # libwinpthread can't be linked statically with this toolchain, so ship the dll next to the exe
    get_filename_component(MINGW_BIN_DIR ${CMAKE_CXX_COMPILER} DIRECTORY)
    if (EXISTS ${MINGW_BIN_DIR}/libwinpthread-1.dll)
        add_custom_command(TARGET MarketServer POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different ${MINGW_BIN_DIR}/libwinpthread-1.dll $<TARGET_FILE_DIR:MarketServer>)
    endif ()
endif ()
