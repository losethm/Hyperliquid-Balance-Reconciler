import unittest
from decimal import Decimal

from hl_reconciler.assets import (
    combine_deltas,
    current_spot_balances,
    fill_forward_deltas,
    funding_forward_deltas,
    ledger_forward_deltas,
    reconstruct_from_current,
    spot_pair_map,
)


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "tokens": [
                {"index": 0, "name": "USDC"},
                {"index": 10, "name": "USOL"},
            ],
            "universe": [
                {"index": 156, "name": "@156", "tokens": [10, 0]},
            ],
        }

    def test_spot_buy_forward_delta(self):
        pairs = spot_pair_map(self.meta)
        fills = [
            {
                "coin": "@156",
                "dir": "Buy",
                "side": "B",
                "sz": "2",
                "px": "80",
                "fee": "0.01",
                "feeToken": "USOL",
            }
        ]
        delta, warnings = fill_forward_deltas(fills, pairs)
        self.assertEqual(delta["USOL"], Decimal("1.99"))
        self.assertEqual(delta["USDC"], Decimal("-160"))
        self.assertEqual(warnings, [])

    def test_outcome_buy_maps_hash_coin_to_plus_token(self):
        fills = [
            {
                "coin": "#1880",
                "dir": "Buy",
                "side": "B",
                "sz": "10",
                "px": "0.4",
                "fee": "0.02",
                "feeToken": "USDC",
            }
        ]
        delta, warnings = fill_forward_deltas(fills, {})
        self.assertEqual(delta["+1880"], Decimal("10"))
        self.assertEqual(delta["USDC"], Decimal("-4.02"))
        self.assertEqual(warnings, [])

    def test_perp_fee_is_inclusive_of_builder_fee(self):
        fills = [
            {
                "coin": "BTC",
                "dir": "Close Long",
                "side": "A",
                "sz": "1",
                "px": "100",
                "closedPnl": "25",
                "fee": "1",
                "builderFee": "0.5",
                "feeToken": "USDC",
            }
        ]
        delta, _ = fill_forward_deltas(fills, {})
        self.assertEqual(delta["USDC"], Decimal("24"))
        self.assertNotIn("BTC", delta)

    def test_transfer_is_reversed_from_current(self):
        wallet = "0xabc"
        ledger = [
            {
                "time": 1,
                "delta": {
                    "type": "spotTransfer",
                    "user": wallet,
                    "destination": "0xdef",
                    "token": "HYPE",
                    "amount": "10",
                    "fee": "1",
                    "feeToken": "USDC",
                    "nativeTokenFee": "0",
                },
            }
        ]
        ledger_delta, _, warnings = ledger_forward_deltas(ledger, wallet)
        historical = reconstruct_from_current(
            {"HYPE": Decimal("5"), "USDC": Decimal("20")},
            ledger_delta,
        )
        self.assertEqual(historical["HYPE"], Decimal("15"))
        self.assertEqual(historical["USDC"], Decimal("21"))
        self.assertEqual(warnings, [])

    def test_combined_backward_reconstruction(self):
        current = current_spot_balances({"balances": [{"coin": "USDC", "total": "100"}]})
        fills, _ = fill_forward_deltas(
            [{"coin": "ETH", "dir": "Close Long", "closedPnl": "20", "fee": "2", "feeToken": "USDC"}],
            {},
        )
        funding = funding_forward_deltas([{"delta": {"usdc": "-3"}}])
        historical = reconstruct_from_current(current, combine_deltas(fills, funding))
        self.assertEqual(historical["USDC"], Decimal("85"))


if __name__ == "__main__":
    unittest.main()
