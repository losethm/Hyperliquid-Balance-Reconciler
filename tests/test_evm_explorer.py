from decimal import Decimal
import unittest

from hl_reconciler.evm_explorer import replay_erc20_balances, replay_native_hype


class ExplorerReplayTests(unittest.TestCase):
    def test_replay_erc20_balances(self):
        wallet = "0xabc"
        rows = [
            {"contractAddress": "0xtoken", "from": "0xdef", "to": wallet, "value": "2500000", "tokenDecimal": "6", "tokenSymbol": "USDC", "tokenName": "USD Coin"},
            {"contractAddress": "0xtoken", "from": wallet, "to": "0xdef", "value": "500000", "tokenDecimal": "6", "tokenSymbol": "USDC", "tokenName": "USD Coin"},
        ]
        result = replay_erc20_balances(wallet, rows)
        self.assertEqual(result["0xtoken"]["balance"], "2")
        self.assertEqual(result["0xtoken"]["raw_balance"], "2000000")

    def test_replay_native_hype_counts_gas_and_internal(self):
        wallet = "0xabc"
        normal = [
            {"from": "0xdef", "to": wallet, "value": str(2 * 10**18), "isError": "0", "gasUsed": "21000", "gasPrice": "1"},
            {"from": wallet, "to": "0xdef", "value": str(10**18), "isError": "0", "gasUsed": "100", "gasPrice": str(10**9)},
        ]
        internal = [
            {"from": "0xdef", "to": wallet, "value": str(5 * 10**17), "isError": "0"},
        ]
        result = replay_native_hype(wallet, normal, internal)
        self.assertEqual(result, Decimal("1.4999999"))


if __name__ == "__main__":
    unittest.main()
