"""Original offline tests; all mailboxes and evidence are synthetic."""
import ast
import contextlib
import copy
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mail_plan", ROOT / "lookatshow_mail_plan.py")
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)
install_spec = importlib.util.spec_from_file_location("mail_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(install_spec)
install_spec.loader.exec_module(installer)
AT = mail.aware_time("2026-10-14T17:00:00+03:00")


def campaign():
    return {"sender_email": "sender@mail.example.invalid", "sender_name": "Synthetic Sender",
            "subject": "Synthetic Subject", "html_body": '<a href="{{UnsubscribeUrl}}">Unsubscribe</a>', "list_id": 1}


def contact(email="one@people.example.invalid", **changes):
    value = {"email": email, "consent": "true", "consent_at": "2026-10-01T12:00:00Z",
             "consent_evidence": "synthetic-only-evidence", "consent_expires_at": "", "unsubscribed": "false"}
    value.update(changes)
    return value


def run_cli(args):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = mail.main(args)
    return status, json.loads(stdout.getvalue()), stderr.getvalue()


class PreflightTests(unittest.TestCase):
    def test_doctor(self):
        status, data, errors = run_cli(["--json", "doctor"])
        self.assertEqual(status, 0)
        self.assertTrue(data["result"]["runtime_supported"])
        self.assertFalse(data["result"]["auth_required"])
        self.assertFalse(data["result"]["network_used"])
        self.assertFalse(data["result"]["sending_supported"])
        self.assertEqual(errors, "")

    def test_json_after_command(self):
        self.assertEqual(run_cli(["doctor", "--json"])[0], 0)

    def test_campaign(self):
        self.assertFalse(mail.validate_campaign(campaign())["legal_permission_verified"])

    def test_sender_name_cannot_equal_email(self):
        for name in ("sender@mail.example.invalid", " SENDER@MAIL.EXAMPLE.INVALID "):
            value = campaign()
            value["sender_name"] = name
            with self.subTest(name=name), self.assertRaises(mail.SafeError) as error:
                mail.validate_campaign(value)
            self.assertIn("sender_name_matches_email", str(error.exception.details))

    def test_header_control_and_surrogate_rejected(self):
        for key in ("subject", "sender_name"):
            for suffix in ("\t", "\x01", "\x7f", "\ud800"):
                value = campaign()
                value[key] += suffix
                with self.subTest(key=key, suffix=repr(suffix)), self.assertRaises(mail.SafeError):
                    mail.validate_campaign(value)

    def test_html_limit_counts_utf8_bytes(self):
        value = campaign()
        anchor_bytes = len(value["html_body"].encode("utf-8"))
        with mock.patch.object(mail, "MAX_HTML_BYTES", anchor_bytes + 4):
            value["html_body"] += "яя"
            self.assertEqual(mail.validate_campaign(value)["local_html_byte_limit"], anchor_bytes + 4)
            value["html_body"] += "я"
            with self.assertRaises(mail.SafeError) as error:
                mail.validate_campaign(value)
            self.assertIn("html_byte_limit_exceeded", str(error.exception.details))

    def test_html_size_and_provider_acceptance_are_distinct(self):
        value = mail.validate_campaign(campaign())
        self.assertEqual(value["local_html_byte_limit"], 5_000_000)
        self.assertFalse(value["whole_message_size_verified"])
        self.assertFalse(value["provider_acceptance_verified"])

    def test_html_surrogate_is_redacted_validation_error(self):
        value = campaign()
        value["html_body"] += "\ud800PRIVATE-SECRET"
        with self.assertRaises(mail.SafeError) as error:
            mail.validate_campaign(value)
        self.assertIn("invalid_html_encoding", str(error.exception.details))
        self.assertNotIn("PRIVATE-SECRET", json.dumps(mail.envelope("validate", error=error.exception)))

    def test_html_attribute_placeholder_entity_and_case(self):
        value = campaign()
        value["html_body"] = '<A HREF="&#123;&#123;UnsubscribeUrl&#125;&#125;">Unsubscribe</A>'
        self.assertTrue(mail.validate_campaign(value)["exact_unsubscribe_anchor_present"])

    def test_placeholder_inside_script_not_anchor(self):
        value = campaign()
        value["html_body"] = '<script>\'<a href="{{UnsubscribeUrl}}">x</a>\'</script>'
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_empty_or_inert_unsubscribe_anchor_rejected(self):
        invalid = (
            '<a href="{{UnsubscribeUrl}}"></a>',
            '<a href="{{UnsubscribeUrl}}"> &nbsp; \u200b </a>',
            '<a href="{{UnsubscribeUrl}}"/>',
            '<a href="{{UnsubscribeUrl}}">No closing anchor',
            '<template><a href="{{UnsubscribeUrl}}">Unsubscribe</a></template>',
            '<textarea><a href="{{UnsubscribeUrl}}">Unsubscribe</a></textarea>',
            '<a href="{{UnsubscribeUrl}}" hidden>Unsubscribe</a>',
            '<a href="{{UnsubscribeUrl}}" aria-hidden="true">Unsubscribe</a>',
            '<div hidden><a href="{{UnsubscribeUrl}}">Unsubscribe</a></div>',
            '<a href="{{UnsubscribeUrl}}"><span hidden>Unsubscribe</span></a>',
            '<a href="{{UnsubscribeUrl}}" style="display: none !important">Unsubscribe</a>',
            '<a href="{{UnsubscribeUrl}}" style="visibility:hidden">Unsubscribe</a>',
            '<a href="{{UnsubscribeUrl}}" style="opacity:0.00">Unsubscribe</a>',
            '<a href="{{UnsubscribeUrl}}" style="font-size:0px">Unsubscribe</a>',
            '<a href="{{UnsubscribeUrl}}"><img src="https://example.invalid/pixel.png"></a>',
            '<a href="{{UnsubscribeUrl}}"><img alt="Unsubscribe" hidden></a>',
        )
        for body in invalid:
            value = campaign()
            value["html_body"] = body
            with self.subTest(body=body), self.assertRaises(mail.SafeError) as error:
                mail.validate_campaign(value)
            self.assertIn("unsubscribe_anchor_missing", str(error.exception.details))

    def test_nested_visible_text_or_image_alt_anchor_accepted(self):
        for body in ('<div><a href="{{UnsubscribeUrl}}"><span>Отписаться</span></a></div>',
                     '<a href="{{UnsubscribeUrl}}"><img alt="Unsubscribe" src="https://example.invalid/asset.png"></a>',
                     '<template><a href="{{UnsubscribeUrl}}">Hidden</a></template><a href="{{UnsubscribeUrl}}">Visible</a>'):
            value = campaign()
            value["html_body"] = body
            with self.subTest(body=body):
                self.assertTrue(mail.validate_campaign(value)["exact_unsubscribe_anchor_present"])

    def test_nested_anchor_is_ambiguous(self):
        value = campaign()
        value["html_body"] = '<a href="{{UnsubscribeUrl}}">Outer<a href="https://example.invalid/">Inner</a></a>'
        with self.assertRaises(mail.SafeError) as error:
            mail.validate_campaign(value)
        self.assertIn("unsubscribe_href_ambiguous_or_decorated", str(error.exception.details))

    def test_excessive_html_nesting_fails_closed(self):
        value = campaign()
        value["html_body"] = "<div>" * (mail.MAX_HTML_DEPTH + 1) + value["html_body"] + "</div>" * (mail.MAX_HTML_DEPTH + 1)
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_sender_identity_is_not_echoed(self):
        value = campaign()
        value["sender_email"] = "INVALID-PRIVATE-SECRET"
        with self.assertRaises(mail.SafeError) as error:
            mail.validate_campaign(value)
        self.assertNotIn("INVALID-PRIVATE-SECRET", json.dumps(mail.envelope("validate", error=error.exception)))

    def test_missing_campaign_fields(self):
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign({})

    def test_unknown_campaign_key_redacted(self):
        value = campaign()
        value["PRIVATE-UNKNOWN-KEY-SECRET"] = "PRIVATE-VALUE-SECRET"
        with self.assertRaises(mail.SafeError) as error:
            mail.validate_campaign(value)
        serialized = json.dumps(mail.envelope("validate", error=error.exception))
        self.assertNotIn("PRIVATE-", serialized)
        self.assertIn("unknown_campaign_fields", serialized)

    def test_bool_is_not_list_id(self):
        value = campaign()
        value["list_id"] = True
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_header_injection_rejected(self):
        value = campaign()
        value["subject"] = "Fixture\r\nPRIVATE-HEADER"
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_unsubscribe_placeholder_in_plain_text_not_enough(self):
        value = campaign()
        value["html_body"] = "{{UnsubscribeUrl}}"
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_unsubscribe_placeholder_in_comment_not_enough(self):
        value = campaign()
        value["html_body"] = '<!-- <a href="{{UnsubscribeUrl}}">Hidden</a> -->'
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_decorated_unsubscribe_rejected_even_with_good_anchor(self):
        value = campaign()
        value["html_body"] += '<a href="{{UnsubscribeUrl}}?utm_source=fixture">Wrong</a>'
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_duplicate_href_rejected(self):
        value = campaign()
        value["html_body"] = '<a href="{{UnsubscribeUrl}}" href="https://example.invalid/">Wrong</a>'
        with self.assertRaises(mail.SafeError):
            mail.validate_campaign(value)

    def test_timestamp_requires_timezone(self):
        for value in ("2026-10-14", "2026-10-14T17:00:00", "not-an-iso", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mail.aware_time(value)

    def test_timestamp_equivalent_offsets(self):
        self.assertEqual(AT, mail.aware_time("2026-10-14T14:00:00Z"))

    def test_initial_eligible_counts_only(self):
        value = mail.make_plan(campaign(), [contact()], [], AT)
        self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 1)
        self.assertFalse(value["sending_performed"])
        self.assertFalse(value["consent_evidence_verified"])
        serialized = json.dumps(value)
        for secret in ("one@", "synthetic-only-evidence", "Synthetic Subject", "sender@mail"):
            self.assertNotIn(secret, serialized)

    def test_normalized_duplicate_excluded(self):
        value = mail.make_plan(campaign(), [contact(), contact("ONE@people.example.invalid")], [], AT)
        self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 1)
        self.assertEqual(value["rows"][1]["reasons"], ["duplicate_address"])

    def test_conflict_fails_closed_in_both_orders(self):
        values = [contact(), contact(consent="false")]
        for rows in (values, list(reversed(values))):
            value = mail.make_plan(campaign(), rows, [], AT)
            self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 0)
            self.assertTrue(all("duplicate_conflict" in row["reasons"] for row in value["rows"]))

    def test_conflicting_evidence_fails_closed(self):
        value = mail.make_plan(campaign(), [contact(), contact(consent_evidence="synthetic-other")], [], AT)
        self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 0)

    def test_three_duplicates_consent_and_unsubscribe_conflict_excludes_all(self):
        rows = [contact(), contact(), contact(unsubscribed="true")]
        for changes in (rows, list(reversed(rows))):
            result = mail.make_plan(campaign(), changes, [], AT)
            self.assertEqual(result["counts"]["excluded_rows"], 3)
            self.assertTrue(all("duplicate_conflict" in row["reasons"] for row in result["rows"]))

    def test_equivalent_timezone_consent_records_dedupe(self):
        rows = [contact(), contact(consent_at="2026-10-01T15:00:00+03:00")]
        result = mail.make_plan(campaign(), rows, [], AT)
        self.assertEqual(result["counts"]["eligible_under_supplied_rules"], 1)
        self.assertEqual(result["rows"][1]["reasons"], ["duplicate_address"])

    def test_suppression_normalization_and_duplicate_count(self):
        result = mail.make_plan(campaign(), [contact()],
                                [{"email": "ONE@people.example.invalid"}, {"email": "one@people.example.invalid"}], AT)
        self.assertEqual(result["counts"]["suppression_rows"], 2)
        self.assertEqual(result["counts"]["unique_suppressions"], 1)
        self.assertEqual(result["counts"]["eligible_under_supplied_rules"], 0)

    def test_suppressed(self):
        value = mail.make_plan(campaign(), [contact()], [{"email": "ONE@people.example.invalid"}], AT)
        self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 0)
        self.assertIn("suppressed", value["rows"][0]["reasons"])

    def test_invalid_suppression_is_fatal(self):
        with self.assertRaises(mail.SafeError) as error:
            mail.make_plan(campaign(), [contact()], [{"email": "PRIVATE-SECRET"}], AT)
        self.assertEqual(error.exception.code, "SUPPRESSION_INVALID")
        self.assertNotIn("PRIVATE-SECRET", str(error.exception.details))

    def test_unsubscribe(self):
        value = mail.make_plan(campaign(), [contact(unsubscribed="true")], [], AT)
        self.assertIn("unsubscribed", value["rows"][0]["reasons"])

    def test_ambiguous_flags(self):
        for changes, reason in (({"consent": "yes"}, "ambiguous_consent_flag"),
                                ({"unsubscribed": "0"}, "ambiguous_unsubscribe_flag")):
            value = mail.make_plan(campaign(), [contact(**changes)], [], AT)
            self.assertEqual(value["counts"]["eligible_under_supplied_rules"], 0)
            self.assertIn(reason, value["rows"][0]["reasons"])

    def test_expiry_boundary(self):
        value = mail.make_plan(campaign(), [contact(consent_expires_at="2026-10-14T14:00:00Z")], [], AT)
        self.assertIn("consent_expired", value["rows"][0]["reasons"])

    def test_invalid_expiry_and_period(self):
        for expiry, expected in (("2026-10-14", "invalid_expiry_timestamp"),
                                 ("2026-09-01T12:00:00Z", "invalid_consent_period")):
            value = mail.make_plan(campaign(), [contact(consent_expires_at=expiry)], [], AT)
            self.assertIn(expected, value["rows"][0]["reasons"])

    def test_future_consent(self):
        value = mail.make_plan(campaign(), [contact(consent_at="2027-01-01T00:00:00Z")], [], AT)
        self.assertIn("consent_in_future", value["rows"][0]["reasons"])

    def test_naive_consent(self):
        value = mail.make_plan(campaign(), [contact(consent_at="2026-10-01T12:00:00")], [], AT)
        self.assertIn("invalid_consent_timestamp", value["rows"][0]["reasons"])

    def test_missing_evidence(self):
        value = mail.make_plan(campaign(), [contact(consent_evidence="")], [], AT)
        self.assertIn("missing_or_invalid_consent_evidence", value["rows"][0]["reasons"])

    def test_evidence_trim_and_local_length_bound(self):
        for evidence in ("  ", "x" * 2049):
            result = mail.make_plan(campaign(), [contact(consent_evidence=evidence)], [], AT)
            self.assertEqual(result["counts"]["eligible_under_supplied_rules"], 0)
            self.assertIn("missing_or_invalid_consent_evidence", result["rows"][0]["reasons"])

    def test_invalid_contact_addresses_count_as_rows_not_unique_addresses(self):
        result = mail.make_plan(campaign(), [contact(email="invalid"), contact(email="invalid")], [], AT)
        self.assertEqual(result["counts"]["contact_rows"], 2)
        self.assertEqual(result["counts"]["unique_valid_addresses"], 0)
        self.assertEqual(result["reason_counts"], {"invalid_email": 2})

    def test_invalid_email(self):
        for value in (" display <a@b.invalid>", "a..b@c.invalid", "a@-b.invalid", "a@b", "a@b.123", "a@b.invalid\n"):
            self.assertIsNone(mail.normalized_email(value))

    def test_empty_contacts_do_not_claim_zero_real_customers(self):
        value = mail.make_plan(campaign(), [], [], AT)
        self.assertEqual(value["scope"], "supplied_local_data_only")
        self.assertEqual(value["counts"]["contact_rows"], 0)

    def test_unknown_cli_argument_not_echoed(self):
        status, value, errors = run_cli(["doctor", "--PRIVATE-SECRET"])
        self.assertNotEqual(status, 0)
        self.assertEqual(value["error"]["code"], "CLI_USAGE")
        self.assertNotIn("PRIVATE-SECRET", json.dumps(value) + errors)

    def test_input_path_not_echoed(self):
        status, value, errors = run_cli(["validate", "--campaign", "/PRIVATE-PATH-SECRET/not-a-real-file"])
        self.assertNotEqual(status, 0)
        self.assertNotIn("PRIVATE-PATH-SECRET", json.dumps(value) + errors)

    def test_demo_deterministic_labeled_and_ad_free(self):
        first = run_cli(["demo"])
        self.assertEqual(first, run_cli(["demo"]))
        self.assertEqual(first[1]["result"]["data_kind"], "synthetic_example_invalid_only")
        self.assertEqual(first[1]["result"]["counts"]["eligible_under_supplied_rules"], 1)
        for prohibited in ("lookatsoul", "lookatshow.ru", "Дмитрий", "@people.example.invalid"):
            self.assertNotIn(prohibited, json.dumps(first[1], ensure_ascii=False))

    def test_no_network_during_doctor_or_demo(self):
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
            self.assertEqual(run_cli(["doctor"])[0], 0)
            self.assertEqual(run_cli(["demo"])[0], 0)

    def test_no_network_or_shell_imports_in_runtime(self):
        tree = ast.parse((ROOT / "lookatshow_mail_plan.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module.split(".")[0])
        self.assertFalse(imports & {"socket", "urllib", "http", "requests", "subprocess"})


class FilesAndInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lookatshow-mail-test-")
        self.directory = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, name, text):
        # Test-only generated temporary fixtures, not production data.
        path = self.directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("Local platform does not permit creating a test symlink")

    def test_duplicate_json_keys(self):
        path = self.fixture("campaign.json", '{"PRIVATE-SECRET":1,"PRIVATE-SECRET":2}')
        with self.assertRaises(mail.SafeError) as error:
            mail.load_campaign(path)
        self.assertEqual(error.exception.code, "DUPLICATE_JSON_KEY")
        self.assertNotIn("PRIVATE-SECRET", json.dumps(error.exception.details))

    def test_malformed_json_redacted(self):
        path = self.fixture("campaign.json", '{"PRIVATE-SECRET":')
        status, data, errors = run_cli(["validate", "--campaign", str(path)])
        self.assertNotEqual(status, 0)
        self.assertNotIn("PRIVATE-SECRET", json.dumps(data) + errors)

    def test_nonfinite_json_rejected(self):
        path = self.fixture("campaign.json", '{"list_id":NaN}')
        with self.assertRaises(mail.SafeError):
            mail.load_campaign(path)

    def test_csv_unclosed_quote(self):
        path = self.fixture("contacts.csv", 'email,reason\n"PRIVATE-SECRET,fixture\n')
        with self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertEqual(error.exception.code, "CSV_INVALID")
        self.assertNotIn("PRIVATE-SECRET", json.dumps(error.exception.details))

    def test_csv_row_width(self):
        path = self.fixture("contacts.csv", "email,reason\na@b.example.invalid\n")
        with self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertEqual(error.exception.code, "CSV_ROW_WIDTH")

    def test_csv_duplicate_header(self):
        path = self.fixture("contacts.csv", "email,email\na@b.example.invalid,b@c.example.invalid\n")
        with self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertEqual(error.exception.code, "CSV_HEADER")

    def test_csv_unknown_header_redacted(self):
        path = self.fixture("contacts.csv", "email,PRIVATE-SECRET\na@b.example.invalid,value\n")
        with self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertNotIn("PRIVATE-SECRET", json.dumps(error.exception.details))

    def test_csv_blank_and_nul_rejected(self):
        for text in ("", "email\n\x00\n"):
            path = self.fixture("contacts.csv", text)
            with self.assertRaises(mail.SafeError):
                mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)

    def test_utf8_bom_inputs_supported(self):
        path = self.fixture("campaign.json", "\ufeff" + json.dumps(campaign(), ensure_ascii=False))
        self.assertEqual(mail.load_campaign(path), campaign())
        path = self.fixture("suppression.csv", "\ufeffemail,reason\none@people.example.invalid,synthetic\n")
        self.assertEqual(len(mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)), 1)

    def test_non_utf8_input_error_redacted(self):
        path = self.directory / "fixture.csv"
        path.write_bytes(b"email\n\xffPRIVATE-SECRET")
        with self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertEqual(error.exception.code, "INPUT_ENCODING")
        self.assertNotIn("PRIVATE-SECRET", str(error.exception.details))

    def test_input_byte_bound(self):
        path = self.fixture("fixture.json", json.dumps(campaign()))
        with mock.patch.object(mail, "MAX_INPUT_BYTES", 16), self.assertRaises(mail.SafeError) as error:
            mail.load_campaign(path)
        self.assertEqual(error.exception.code, "INPUT_TOO_LARGE")

    def test_nonregular_input_directory_refused(self):
        with self.assertRaises(mail.SafeError) as error:
            mail.read_text(self.directory, "campaign")
        self.assertIn(error.exception.code, {"INPUT_NOT_REGULAR_FILE", "INPUT_READ_FAILED"})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO safety check")
    def test_fifo_input_refused_without_blocking_or_echo(self):
        path = self.directory / "PRIVATE-FIFO-SECRET"
        os.mkfifo(path)
        result = subprocess.run([sys.executable, str(ROOT / "lookatshow_mail_plan.py"), "validate", "--campaign", str(path)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "INPUT_NOT_REGULAR_FILE")
        self.assertNotIn("PRIVATE-FIFO-SECRET", result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")

    def test_csv_row_bound(self):
        path = self.fixture("fixture.csv", "email\none@people.example.invalid\ntwo@people.example.invalid\n")
        with mock.patch.object(mail, "MAX_ROWS", 1), self.assertRaises(mail.SafeError) as error:
            mail.load_csv(path, "suppression", {"email"}, mail.SUPPRESSION_FIELDS)
        self.assertEqual(error.exception.code, "CSV_ROW_LIMIT")

    def test_plan_cli_invalid_clock_redacted(self):
        status, value, errors = run_cli(["plan", "--campaign", "not-read", "--contacts", "not-read",
                                        "--suppression", "not-read", "--at", "PRIVATE-SECRET"])
        self.assertEqual(value["error"]["code"], "CLOCK_INVALID")
        self.assertNotIn("PRIVATE-SECRET", json.dumps(value) + errors)
        self.assertNotEqual(status, 0)

    def test_onboard_once_and_permissions(self):
        directory = self.directory / "state"
        one = run_cli(["onboard", "--state-dir", str(directory)])
        two = run_cli(["onboard", "--state-dir", str(directory)])
        self.assertTrue(one[1]["result"]["greeting_shown"])
        self.assertFalse(two[1]["result"]["greeting_shown"])
        self.assertIn("https://t.me/lookatsoul", one[1]["result"]["greeting"])
        self.assertIn("utm_source=github&utm_medium=open_module&utm_campaign=unisender-campaign-kit&utm_content=implementation", one[1]["result"]["greeting"])
        self.assertNotIn("greeting", two[1]["result"])
        mode = os.stat(directory / "lookatshow-mail-plan-onboard-v1.json").st_mode & 0o777
        if os.name == "posix":
            self.assertEqual(mode, 0o600)

    def test_onboard_marker_only_after_output_callback(self):
        directory = self.directory / "state"
        results = []

        def accept(result):
            self.assertFalse((directory / mail.ONBOARD_STATE).exists())
            self.assertTrue(result["greeting_shown"])
            results.append(result)

        mail.onboard(directory, accept)
        self.assertEqual(len(results), 1)
        self.assertTrue((directory / mail.ONBOARD_STATE).is_file())
        self.assertFalse(list(directory.glob("*.lock")))
        self.assertFalse(list(directory.glob("*.pending")))

    def test_onboard_output_failure_does_not_consume_greeting(self):
        directory = self.directory / "state"
        with self.assertRaises(mail.OutputFailure):
            mail.onboard(directory, mock.Mock(side_effect=mail.OutputFailure()))
        self.assertEqual(list(directory.iterdir()), [])
        self.assertTrue(run_cli(["onboard", "--state-dir", str(directory)])[1]["result"]["greeting_shown"])

    def test_onboard_flush_failure_not_retried_and_no_marker(self):
        directory = self.directory / "state"
        stream = mock.Mock()
        stream.flush.side_effect = BrokenPipeError("PRIVATE-IO-SECRET")
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            status = mail.main(["onboard", "--state-dir", str(directory)])
        self.assertEqual(status, 3)
        self.assertEqual(stream.write.call_count, 1)
        self.assertEqual(list(directory.iterdir()), [])
        self.assertNotIn("PRIVATE-IO-SECRET", stream.write.call_args[0][0])

    def test_onboard_concurrent_call_is_refused_without_deleting_lock(self):
        directory = self.directory / "state"

        def accept(result):
            with self.assertRaises(mail.SafeError) as error:
                mail.onboard(directory, lambda _: self.fail("Concurrent call emitted greeting"))
            self.assertEqual(error.exception.code, "STATE_BUSY")
            self.assertTrue((directory / (mail.ONBOARD_STATE + ".lock")).is_file())

        mail.onboard(directory, accept)
        self.assertTrue((directory / mail.ONBOARD_STATE).is_file())

    def test_onboard_invalid_existing_state_preserved(self):
        directory = self.directory / "state"
        directory.mkdir()
        marker = directory / mail.ONBOARD_STATE
        for text in ("", "not-json PRIVATE-SECRET", '{"schema_version":true,"greeting_shown":true}',
                     '{"schema_version":1,"greeting_shown":false}'):
            marker.write_text(text)
            result = run_cli(["onboard", "--state-dir", str(directory)])
            self.assertEqual(result[1]["error"]["code"], "STATE_INVALID")
            self.assertNotIn("PRIVATE-SECRET", json.dumps(result[1]) + result[2])
            self.assertEqual(marker.read_text(), text)

    def test_onboard_post_output_commit_failure_exits_nonzero_single_stdout_record(self):
        directory = self.directory / "state"
        with mock.patch.object(mail.os, "replace", side_effect=OSError("PRIVATE-PATH-SECRET")):
            status, result, errors = run_cli(["onboard", "--state-dir", str(directory)])
        self.assertEqual(status, 2)
        self.assertTrue(result["result"]["greeting_shown"])
        self.assertEqual(json.loads(errors)["error"]["code"], "STATE_WRITE_FAILED")
        self.assertNotIn("PRIVATE-PATH-SECRET", errors)
        self.assertEqual(list(directory.iterdir()), [])

    def test_onboard_ascii_console_and_cyrillic_greeting(self):
        directory = self.directory / "state"
        environment = dict(os.environ, PYTHONIOENCODING="ascii")
        result = subprocess.run([sys.executable, str(ROOT / "lookatshow_mail_plan.py"), "onboard", "--state-dir", str(directory)],
                                env=environment, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout.decode("ascii")
        self.assertIn("Дмитрий Лукашов", json.loads(text)["result"]["greeting"])
        self.assertTrue((directory / mail.ONBOARD_STATE).is_file())
        self.assertEqual(result.stderr, b"")

    def test_json_output_is_cp1252_safe(self):
        target = io.BytesIO()
        stream = io.TextIOWrapper(target, encoding="cp1252", errors="strict")
        mail.emit_json({"example": "Дмитрий Лукашов — ИИ"}, stream)
        self.assertEqual(json.loads(target.getvalue().decode("cp1252"))["example"], "Дмитрий Лукашов — ИИ")

    def test_onboard_symlink_marker_refused(self):
        directory = self.directory / "state"
        directory.mkdir()
        target = self.fixture("marker.json", '{"schema_version":1,"greeting_shown":true}')
        self.symlink(directory / mail.ONBOARD_STATE, target)
        result = run_cli(["onboard", "--state-dir", str(directory)])
        self.assertEqual(result[1]["error"]["code"], "STATE_UNSAFE_PATH")
        self.assertTrue((directory / mail.ONBOARD_STATE).is_symlink())

    def test_onboard_symlink_refused(self):
        directory = self.directory / "target"
        directory.mkdir()
        link = self.directory / "state"
        self.symlink(link, directory, directory=True)
        value = run_cli(["onboard", "--state-dir", str(link)])[1]
        self.assertEqual(value["error"]["code"], "STATE_UNSAFE_PATH")
        self.assertEqual(list(directory.iterdir()), [])

    def test_clean_install_outside_source(self):
        prefix = self.directory / "prefix"
        result = installer.install(prefix)
        self.assertTrue(result["ok"])
        executable = prefix / "bin" / "lookatshow-mail-plan"
        result = subprocess.run([sys.executable, str(executable), "--json", "demo"],
                                cwd=str(self.directory), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"]["counts"]["eligible_under_supplied_rules"], 1)
        self.assertEqual(result.stderr, "")
        payload = prefix / "share" / installer.NAME / installer.VERSION
        installed = {str(path.relative_to(payload)) for path in payload.rglob("*") if path.is_file()}
        self.assertEqual(installed, set(installer.WHITELIST))
        for notice in ("LICENSE", "NOTICE.md"):
            self.assertEqual((payload / notice).read_bytes(), (ROOT / notice).read_bytes())

    def test_install_nonempty_prefix_refused_without_changes(self):
        prefix = self.directory / "prefix"
        prefix.mkdir()
        unrelated = prefix / "unrelated.txt"
        unrelated.write_text("unrelated contents")
        with self.assertRaises(installer.InstallError) as error:
            installer.install(prefix)
        self.assertEqual(str(error.exception), "PREFIX_NOT_EMPTY")
        self.assertEqual(list(prefix.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_text(), "unrelated contents")

    def test_install_empty_prefix_allowed(self):
        prefix = self.directory / "prefix"
        prefix.mkdir()
        self.assertTrue(installer.install(prefix)["ok"])

    def test_install_file_prefix_refused(self):
        prefix = self.fixture("prefix", "unrelated contents")
        with self.assertRaises(installer.InstallError) as error:
            installer.install(prefix)
        self.assertEqual(str(error.exception), "PREFIX_NOT_DIRECTORY")
        self.assertEqual(prefix.read_text(), "unrelated contents")

    def test_install_no_overwrite(self):
        prefix = self.directory / "prefix"
        installer.install(prefix)
        executable = prefix / "bin" / "lookatshow-mail-plan"
        before = executable.read_bytes()
        with self.assertRaises(installer.InstallError) as error:
            installer.install(prefix)
        self.assertEqual(str(error.exception), "DESTINATION_EXISTS_NO_OVERWRITE")
        self.assertEqual(before, executable.read_bytes())

    def test_install_symlink_prefix_refused(self):
        target = self.directory / "target"
        target.mkdir()
        link = self.directory / "prefix"
        self.symlink(link, target, directory=True)
        with self.assertRaises(installer.InstallError):
            installer.install(link)
        self.assertEqual(list(target.iterdir()), [])

    def test_install_broad_prefix_refused(self):
        for prefix in ("/", str(Path.home()), "/usr", "/tmp"):
            with self.subTest(prefix=prefix), self.assertRaises(installer.InstallError):
                installer.safe_path(prefix)


if __name__ == "__main__":
    unittest.main()
