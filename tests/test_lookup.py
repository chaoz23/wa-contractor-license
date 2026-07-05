import io
import json
import unittest
from contextlib import redirect_stdout
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


class BatchExitCodeTests(unittest.TestCase):
    def test_all_found_or_empty_batch_succeeds(self):
        self.assertEqual(lookup._batch_exit_code([]), 0)
        self.assertEqual(lookup._batch_exit_code([{"action": "found"}]), 0)

    def test_non_matches_and_retryable_errors_exit_one(self):
        for action in ("pick", "none", "refine"):
            with self.subTest(action=action):
                self.assertEqual(lookup._batch_exit_code([{"action": action}]), 1)

    def test_reject_takes_precedence(self):
        results = [{"action": "refine"}, {"action": "reject"}]
        self.assertEqual(lookup._batch_exit_code(results), 2)

    @patch("lookup.batch_lookup")
    def test_batch_cli_emits_every_result_before_nonzero_exit(self, batch_lookup):
        results = [
            {"action": "found", "input": "Acme", "results": [{}]},
            {"action": "reject", "input": "A", "results": []},
        ]
        batch_lookup.return_value = results
        output = io.StringIO()

        with (
            patch.object(lookup.sys, "argv", ["lookup.py", "--batch"]),
            patch.object(lookup.sys, "stdin", io.StringIO("Acme\nA\n")),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            lookup.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(
            [json.loads(line) for line in output.getvalue().splitlines()],
            results,
        )


if __name__ == "__main__":
    unittest.main()
