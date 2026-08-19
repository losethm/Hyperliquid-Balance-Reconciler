import unittest

from hl_reconciler.hyperevm import find_block_at_or_before, wei_to_hype


class FakeRpc:
    def __init__(self, timestamps):
        self.timestamps = timestamps

    def block_number(self):
        return max(self.timestamps)

    def block(self, number):
        return {"timestamp": hex(self.timestamps[number])}


class HyperEvmTests(unittest.TestCase):
    def test_binary_search(self):
        rpc = FakeRpc({0: 10, 1: 20, 2: 30, 3: 40, 4: 50})
        found = find_block_at_or_before(rpc, 34)
        self.assertEqual(found.number, 2)
        self.assertEqual(found.timestamp_s, 30)

    def test_wei_conversion(self):
        self.assertEqual(wei_to_hype(895223499927000000), "0.895223499927")


if __name__ == "__main__":
    unittest.main()
