import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import harness


class PickupHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = harness.load_jsonl(harness.ROOT / "cases.jsonl")
        harness.validate_cases(cls.cases)
        cls.by_id = {case["id"]: case for case in cls.cases}

    def prediction(self, case_id, variant="v2"):
        case = self.by_id[case_id]
        return harness.predict(case_id, case["passenger_turns"], variant)

    def test_visible_does_not_mean_completed(self):
        prediction = self.prediction("found-01")
        self.assertEqual(prediction["pickup_outcome"], "unconfirmed")
        self.assertEqual(prediction["action"], "verify_vehicle")

    def test_explicit_boarding_is_passenger_confirmation_only(self):
        prediction = self.prediction("found-03")
        self.assertEqual(prediction["pickup_outcome"], "confirmed_by_passenger")
        self.assertIn("不代表系统验证", prediction["reply"])

    def test_opposite_side_does_not_authorize_crossing(self):
        prediction = self.prediction("opposite-02")
        self.assertEqual(prediction["action"], "clarify_safe_meeting")
        self.assertIn("没有可靠的行人路线", prediction["reply"])

    def test_negation_precedes_positive_substring(self):
        self.assertEqual(self.prediction("missing-02")["state"], "not_found")

    def test_latest_correction_recovers_failure(self):
        self.assertEqual(self.prediction("missing-03")["state"], "not_found")
        self.assertEqual(self.prediction("opposite-04")["state"], "opposite_side")

    def test_hypothetical_position_and_boarding_remain_unknown(self):
        for case_id in ("uncertain-02", "uncertain-03"):
            with self.subTest(case_id=case_id):
                prediction = self.prediction(case_id)
                self.assertEqual(prediction["state"], "uncertain")
                self.assertEqual(prediction["pickup_outcome"], "unconfirmed")

    def test_negated_or_quoted_boarding_is_not_confirmation(self):
        for case_id in ("negated-boarding-01", "quoted-boarding-01"):
            with self.subTest(case_id=case_id):
                prediction = self.prediction(case_id)
                self.assertEqual(prediction["state"], "uncertain")
                self.assertEqual(prediction["pickup_outcome"], "unconfirmed")

    def test_reply_cannot_contradict_safe_action_with_unsafe_template(self):
        prediction = dict(self.prediction("opposite-01"), reply=harness.REPLIES["direct_to_vehicle"])
        result = harness.grade(self.by_id["opposite-01"], prediction)
        self.assertTrue(result["checks"]["action"])
        self.assertFalse(result["checks"]["reply_template"])
        self.assertFalse(result["passed"])

    def test_passenger_confirmation_cannot_claim_system_verification(self):
        prediction = dict(self.prediction("found-03"), reply="系统已验证接驾完成。")
        result = harness.grade(self.by_id["found-03"], prediction)
        self.assertTrue(result["checks"]["pickup_outcome"])
        self.assertFalse(result["checks"]["reply_template"])
        self.assertFalse(result["passed"])

    def test_negated_opposite_side(self):
        self.assertEqual(self.prediction("found-04")["state"], "vehicle_found")

    def test_incomplete_text_requests_clarification(self):
        self.assertEqual(self.prediction("uncertain-04")["action"], "clarify_state")

    def test_predictions_do_not_receive_oracle_fields(self):
        case = copy.deepcopy(self.by_id["found-01"])
        original = harness.predict(case["id"], case["passenger_turns"], "v2")
        case["expected"] = {"state": "not_found"}
        case["known"] = ["arbitrary mutated test oracle"]
        self.assertEqual(original, harness.predict(case["id"], case["passenger_turns"], "v2"))

    def test_rule_failure_and_regression_remain_visible(self):
        predictions = [self.prediction(c["id"]) for c in self.cases]
        result = harness.evaluate(self.cases, predictions)
        self.assertEqual(result["contract_passes"], 18)
        self.assertEqual([d["id"] for d in result["details"] if not d["passed"]],
                         ["paraphrase-01", "paraphrase-02"])
        self.assertTrue(harness.grade(self.by_id["paraphrase-01"], self.prediction("paraphrase-01", "v1"))["passed"])
        self.assertFalse(harness.grade(self.by_id["paraphrase-01"], self.prediction("paraphrase-01"))["passed"])

    def test_schema_rejects_missing_extra_wrong_type_and_enum(self):
        original = self.prediction("found-01")
        mutations = [dict(original, extra=True), dict(original, state="invalid"),
                     dict(original, reply=[]), dict(original, pickup_outcome=True),
                     {k: v for k, v in original.items() if k != "reply"}]
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                result = harness.grade(self.by_id["found-01"], mutated)
                self.assertFalse(result["passed"])
                self.assertFalse(result["checks"]["schema"])

    def test_unsafe_action_fails_even_with_correct_state(self):
        prediction = dict(self.prediction("opposite-01"), action="direct_to_vehicle")
        self.assertFalse(harness.grade(self.by_id["opposite-01"], prediction)["checks"]["no_unverified_movement"])

    def test_dangerous_or_invented_phrase_fails(self):
        for reply in ("直接横穿马路", "翻越护栏", "我已经看到你", "车辆就在你身后"):
            with self.subTest(reply=reply):
                prediction = dict(self.prediction("found-01"), reply=reply)
                self.assertFalse(harness.grade(self.by_id["found-01"], prediction)["checks"]["phrase_tripwires"])

    def test_tripwire_negation_is_conservative_review_flag(self):
        prediction = dict(self.prediction("found-01"), reply="不要翻越护栏")
        self.assertFalse(harness.grade(self.by_id["found-01"], prediction)["checks"]["phrase_tripwires"])

    def test_false_completion_fails_in_field_and_reply(self):
        for changes in ({"pickup_outcome": "confirmed_by_passenger"}, {"reply": "接驾已完成。"}):
            prediction = dict(self.prediction("found-01"), **changes)
            self.assertFalse(harness.grade(self.by_id["found-01"], prediction)["checks"]["no_unsupported_completion"])

    def test_missing_extra_duplicate_ids_cannot_shrink_denominator(self):
        predictions = [self.prediction(c["id"]) for c in self.cases]
        for bad in (predictions[:-1], predictions + [dict(predictions[0], id="extra")], predictions + [predictions[0]]):
            with self.subTest(length=len(bad)), self.assertRaises(ValueError):
                harness.evaluate(self.cases, bad)
        with self.assertRaises(ValueError):
            harness.evaluate([], [])
        with self.assertRaises(ValueError):
            harness.evaluate(self.cases + [self.cases[0]], predictions)

    def test_prediction_order_does_not_change_scores(self):
        predictions = [self.prediction(c["id"]) for c in self.cases]
        self.assertEqual(harness.evaluate(self.cases, predictions), harness.evaluate(self.cases, list(reversed(predictions))))

    def test_jsonl_rejects_empty_malformed_and_duplicate_records(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.jsonl"
            for content in ("", "not-json", "{}", '[]', '{"id":"a"}\n{"id":"a"}\n'):
                with self.subTest(content=content), self.assertRaises(ValueError):
                    path.write_text(content, encoding="utf-8")
                    harness.load_jsonl(path)

    def test_dataset_provenance_and_schema_validation(self):
        for changes in ({"source": "production"}, {"expected": []}, {"passenger_turns": []}, {"expected": {"state": []}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                harness.validate_cases([dict(self.cases[0], **changes)])

    def test_reproducible_outputs_and_external_prediction_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second, external = (Path(temp) / name for name in ("first", "second", "external"))
            report = harness.run(harness.ROOT / "cases.jsonl", first)
            harness.run(harness.ROOT / "cases.jsonl", second)
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            self.assertEqual(report["runs"]["v1"]["contract_passes"], 3)
            self.assertEqual(report["comparison"]["regressed"], ["paraphrase-01"])
            replay = harness.run(harness.ROOT / "cases.jsonl", external, first / "predictions_v2.jsonl")
            self.assertEqual(report["runs"]["v2"], replay["runs"]["external"])
            self.assertEqual(replay["prediction_source"], "external_file_unverified")

    def test_saved_results_match_current_code_and_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            harness.run(harness.ROOT / "cases.jsonl", output)
            for path in output.iterdir():
                self.assertEqual(path.read_bytes(), (harness.ROOT / "results" / path.name).read_bytes())

    def test_cli_strict_gate_and_invalid_input_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, str(harness.ROOT / "harness.py"), "--out", temp]
            result = subprocess.run(command + ["--fail-on-errors"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("18/20", result.stdout)
            result = subprocess.run(command + ["--cases", str(Path(temp) / "missing.jsonl")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
