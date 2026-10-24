# MarketServer protocol test

From `book/OrderBook`, after building:

```
./build/MarketServer.exe < tests/protocol_input.txt > build/protocol_actual.txt
diff --strip-trailing-cr tests/expected_output.txt build/protocol_actual.txt && echo PROTOCOL OK
```
