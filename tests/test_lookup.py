import unittest
from unittest.mock import ANY, Mock, call, patch

import lookup


class InputDetectionTests(unittest.TestCase):
    def test_alphabetic_search_term_is_a_name(self):
        self.assertEqual(lookup._detect_input_type("plumbing"), ("Name", "plumbing"))

    def test_license_id_is_normalized(self):
        self.assertEqual(
            lookup._detect_input_type("mortesl763nr"),
            ("LicenseId", "MORTESL763NR"),
        )

    def test_spaced_ubi_is_normalized(self):
        self.assertEqual(
            lookup._detect_input_type("605 417 027"),
            ("Ubi", "605417027"),
        )


class SearchFallbackTests(unittest.TestCase):
    @patch("lookup._do_search")
    def test_missing_license_retries_as_name(self, do_search):
        name_data = {"TotalCount": 1, "SearchResult": [{}]}
        do_search.side_effect = [
            {"TotalCount": 0, "SearchResult": []},
            name_data,
        ]

        opener = Mock()
        search_type, data = lookup._search_with_name_fallback(
            opener, "LicenseId", "ACME123", "Acme123"
        )

        self.assertEqual(search_type, "Name")
        self.assertIs(data, name_data)
        self.assertEqual(
            do_search.call_args_list,
            [call(opener, "LicenseId", "ACME123"), call(opener, "Name", "Acme123")],
        )

    @patch("lookup._do_search")
    def test_license_match_does_not_fall_back(self, do_search):
        license_data = {"TotalCount": 1, "SearchResult": [{}]}
        do_search.return_value = license_data

        search_type, data = lookup._search_with_name_fallback(
            Mock(), "LicenseId", "MORTESL763NR", "MORTESL763NR"
        )

        self.assertEqual(search_type, "LicenseId")
        self.assertIs(data, license_data)
        do_search.assert_called_once()


class LookupTests(unittest.TestCase):
    @patch("lookup._warmup_session")
    @patch("lookup._lni_session")
    @patch("lookup._do_search")
    def test_documented_plumbing_query_uses_name_search(
        self, do_search, lni_session, warmup_session
    ):
        opener = Mock()
        lni_session.return_value = opener
        do_search.return_value = {"TotalCount": 0, "SearchResult": []}

        result = lookup.lookup("plumbing")

        warmup_session.assert_called_once_with(opener)
        do_search.assert_called_once_with(opener, "Name", "plumbing")
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["search_type"], "Name")


if __name__ == "__main__":
    unittest.main()
