from decimal import Decimal
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from hl_reconciler.evm_explorer import (
    EtherscanCompatibleExplorer,
    ExplorerError,
    replay_erc20_balances,
    replay_native_hype,
)
from hl_reconciler.cli import _compare_hyperevm_results


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

    def test_failed_outgoing_transaction_charges_gas_but_not_value(self):
        wallet = "0xabc"
        normal = [
            {
                "from": wallet,
                "to": "0xdef",
                "value": str(10**18),
                "isError": "1",
                "gasUsed": "100",
                "gasPrice": str(10**9),
            }
        ]
        result = replay_native_hype(wallet, normal, [])
        self.assertEqual(result, Decimal("-0.0000001"))

    def test_failed_incoming_transaction_does_not_credit_value(self):
        normal = [
            {
                "from": "0xdef",
                "to": "0xabc",
                "value": str(10**18),
                "isError": "1",
                "gasUsed": "100",
                "gasPrice": str(10**9),
            }
        ]
        self.assertEqual(replay_native_hype("0xabc", normal, []), Decimal("0"))

    def test_account_history_paginates_until_short_page(self):
        pages = {
            "1": [{"hash": "0x1"}, {"hash": "0x2"}],
            "2": [{"hash": "0x3"}],
        }

        def fake_get_json(url, timeout):
            page = parse_qs(urlparse(url).query)["page"][0]
            return {"status": "1", "result": pages[page]}

        explorer = EtherscanCompatibleExplorer("https://example.test/api", "key")
        with patch("hl_reconciler.evm_explorer.get_json", side_effect=fake_get_json):
            rows = explorer.normal_transactions("0xabc", page_size=2)
        self.assertEqual([row["hash"] for row in rows], ["0x1", "0x2", "0x3"])

    def test_account_history_rejects_repeated_full_page(self):
        def fake_get_json(url, timeout):
            return {"status": "1", "result": [{"hash": "0x1"}]}

        explorer = EtherscanCompatibleExplorer("https://example.test/api", "key")
        with patch("hl_reconciler.evm_explorer.get_json", side_effect=fake_get_json):
            with self.assertRaisesRegex(ExplorerError, "repeated page"):
                explorer.normal_transactions("0xabc", page_size=1)

    def test_account_history_rejects_non_list_result(self):
        explorer = EtherscanCompatibleExplorer("https://example.test/api", "key")
        with patch(
            "hl_reconciler.evm_explorer.get_json",
            return_value={"status": "1", "result": "unexpected"},
        ):
            with self.assertRaisesRegex(ExplorerError, "non-list result"):
                explorer.normal_transactions("0xabc")

    def test_no_transactions_response_is_empty(self):
        explorer = EtherscanCompatibleExplorer("https://example.test/api", "key")
        with patch(
            "hl_reconciler.evm_explorer.get_json",
            return_value={
                "status": "0",
                "message": "No Transactions Found",
                "result": "No transactions found",
            },
        ):
            self.assertEqual(explorer.normal_transactions("0xabc"), [])

    def test_archive_comparison_reports_exact_match(self):
        explorer_result = {
            "block_number": 100,
            "native_hype_wei": "42",
            "erc20_balances": [{"contract": "0xToken", "raw_balance": "7"}],
        }
        archive_result = {
            "status": "ok",
            "block_number": 100,
            "native_hype_wei": "42",
            "erc20_balances": [{"token": "0xtoken", "raw_balance": "7"}],
        }
        comparison = _compare_hyperevm_results(explorer_result, archive_result)
        self.assertEqual(comparison["status"], "matched")

    def test_archive_comparison_surfaces_mismatch(self):
        explorer_result = {
            "block_number": 100,
            "native_hype_wei": "42",
            "erc20_balances": [{"contract": "0xtoken", "raw_balance": "7"}],
        }
        archive_result = {
            "status": "ok",
            "block_number": 100,
            "native_hype_wei": "41",
            "erc20_balances": [{"token": "0xtoken", "raw_balance": "6"}],
        }
        comparison = _compare_hyperevm_results(explorer_result, archive_result)
        self.assertEqual(comparison["status"], "mismatch")
        self.assertFalse(comparison["native_hype_matches"])
        self.assertFalse(comparison["erc20_balances_match"])

    def test_archive_only_nonzero_token_surfaces_mismatch(self):
        explorer_result = {
            "block_number": 100,
            "native_hype_wei": "42",
            "erc20_balances": [],
        }
        archive_result = {
            "status": "ok",
            "block_number": 100,
            "native_hype_wei": "42",
            "erc20_balances": [{"token": "0xtoken", "raw_balance": "1"}],
        }
        comparison = _compare_hyperevm_results(explorer_result, archive_result)
        self.assertEqual(comparison["status"], "mismatch")
        self.assertFalse(comparison["erc20_balances_match"])


if __name__ == "__main__":
    unittest.main()
