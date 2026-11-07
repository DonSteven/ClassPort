# Database Systems — Sample Notes

This original sample is provided for the public ClassPort demo.

## Transactions and ACID

- Atomicity: a transaction completes as a unit or rolls back.
- Consistency: committed data respects the application's constraints.
- Isolation: concurrent transactions follow the guarantees of the selected isolation level.
- Durability: committed changes survive a restart under the database's durability configuration.

## A booking example

Two visitors try to reserve the last seat. A transaction and an appropriate concurrency strategy must prevent both requests from successfully taking that seat.

Try explaining which anomalies are possible under different isolation levels.
