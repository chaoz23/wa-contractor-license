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


class ResultFilteringTests(unittest.TestCase):
    def test_non_license_rows_are_excluded_from_pick_results(self):
        data = {
            "TotalCount": 3,
            "SearchResult": [
                {
                    "LicenseId": None,
                    "BusinessName": "ORDINARY BUSINESS RECORD",
                    "ContractorType": None,
                    "ContractorGroup": None,
                    "Status": "Active",
                },
                {
                    "LicenseId": "MORTESL763NR",
                    "BusinessName": "MORTENSON SIGNS, LLC",
                    "ContractorType": "Construction Contractor",
                    "ContractorGroup": "Construction Contractor",
                    "IrlStatusCode": "A",
                },
            ],
        }

        result = lookup._build_result("morris", "Name", data)

        self.assertEqual(result["action"], "pick")
        self.assertEqual(result["total_found"], 3)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["license_id"], "MORTESL763NR")
        self.assertIn("L&I record", result["message"])
        self.assertIn("contractor/license record", result["message"])

    def test_only_non_license_rows_are_reported_as_no_license_match(self):
        data = {
            "TotalCount": 1,
            "SearchResult": [
                {
                    "LicenseId": None,
                    "BusinessName": "WASHINGTON FEDERAL BANK",
                    "ContractorType": None,
                    "ContractorGroup": None,
                    "Status": "Active",
                }
            ],
        }

        result = lookup._build_result("washington federal", "Name", data)

        self.assertEqual(result["action"], "none")
        self.assertEqual(result["total_found"], 1)
        self.assertEqual(result["results"], [])
        self.assertIn("none were contractor/license records", result["message"])

    def test_nullable_fields_format_as_contract_strings(self):
        row = {
            "LicenseId": "MORTESL763NR",
            "BusinessName": None,
            "ContractorType": "Construction Contractor",
            "ContractorGroup": "Construction Contractor",
            "City": None,
            "State": None,
            "Ubi": None,
        }

        result = lookup._format_result(row)

        self.assertEqual(result["business_name"], "")
        self.assertEqual(result["city"], "")
        self.assertEqual(result["state"], "")
        self.assertIsNone(result["ubi"])


class BatchLookupTests(unittest.TestCase):
    @patch("lookup._warmup_session")
    @patch("lookup._lni_session")
    def test_all_invalid_batch_skips_network(self, lni_session, warmup_session):
        results = lookup.batch_lookup(["", " A "])

        self.assertEqual(
            [result["action"] for result in results],
            ["reject", "reject"],
        )
        self.assertEqual([result["input"] for result in results], ["", "A"])
        lni_session.assert_not_called()
        warmup_session.assert_not_called()

    @patch("lookup._warmup_session", side_effect=OSError("offline"))
    @patch("lookup._lni_session")
    def test_invalid_inputs_remain_rejected_when_warmup_fails(
        self, lni_session, warmup_session
    ):
        opener = Mock()
        lni_session.return_value = opener

        results = lookup.batch_lookup([" A ", "Acme Plumbing"])

        self.assertEqual(
            [result["action"] for result in results],
            ["reject", "refine"],
        )
        self.assertEqual(
            [result["input"] for result in results],
            ["A", "Acme Plumbing"],
        )
        warmup_session.assert_called_once_with(opener)


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

    @patch("lookup._warmup_session", side_effect=OSError("offline"))
    @patch("lookup._lni_session")
    def test_batch_cli_preserves_blank_lines_and_validation_order(
        self, lni_session, warmup_session
    ):
        lni_session.return_value = Mock()
        output = io.StringIO()

        with (
            patch.object(lookup.sys, "argv", ["lookup.py", "--batch"]),
            patch.object(lookup.sys, "stdin", io.StringIO("A\n\nAcme Plumbing\n")),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            lookup.main()

        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(
            [result["action"] for result in results],
            ["reject", "reject", "refine"],
        )
        self.assertEqual(
            [result["input"] for result in results],
            ["A", "", "Acme Plumbing"],
        )


if __name__ == "__main__":
    unittest.main()
