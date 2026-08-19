# Asset balance scope

The year-end report is intended to answer: **what assets did the wallet own at the cutoff?**

Primary output:

- HyperCore USDC/cash balance
- HyperCore spot token quantities
- HYPE held in staking
- lending/supply balances and interest where recoverable
- vault equity where recoverable
- HyperEVM native HYPE and ERC-20 balances when archive state is available

Perpetual positions are supporting reconciliation data only. Open notional and unrealized P&L are not the primary output, but realized P&L, fees, and funding after the cutoff must still be reversed because they change the wallet's cash balance.

The first asset-statement implementation reconstructs the master account. Linked subaccounts are the next roll-up layer so internal subaccount transfers can be eliminated and their year-end balances included in the same total.
