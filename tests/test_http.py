import unittest

from hl_reconciler.http import _redact_url


class HttpTests(unittest.TestCase):
    def test_redact_url_hides_credentials_and_api_keys(self):
        redacted = _redact_url(
            "https://user:password@example.test/api?apikey=super-secret&chain_id=999"
        )
        self.assertEqual(
            redacted,
            "https://***@example.test/api?apikey=REDACTED&chain_id=999",
        )
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("password", redacted)


if __name__ == "__main__":
    unittest.main()
