#!/usr/bin/env python3
"""Small, offline, synthetic pickup evaluation. Python standard library only."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATES = {"vehicle_found", "opposite_side", "not_found", "uncertain", "onboarded"}
ACTIONS = {"verify_vehicle", "clarify_safe_meeting", "request_landmark", "clarify_state",
           "acknowledge_onboarding", "direct_to_vehicle"}
OUTCOMES = {"unconfirmed", "confirmed_by_passenger"}
PREDICTION_FIELDS = {"id", "state", "action", "pickup_outcome", "reply"}
REPLIES = {
    "verify_vehicle": "请先核对订单中的车牌和车辆信息；看到车不代表已经完成接驾。",
    "clarify_safe_meeting": "请先留在安全位置，告知你所在的一侧和附近地标，再与司机确认合法接驾点。当前没有可靠的行人路线信息，不能据此建议过马路。",
    "request_landmark": "请先留在安全位置，告诉我附近的地标或出入口，并与司机核对接驾点；我无法确认车辆的实时位置。",
    "clarify_state": "请先留在安全位置。你现在能看到订单中的车辆吗？车辆与你在同一侧还是对面？",
    "acknowledge_onboarding": "收到，你已报告核对车辆并上车；这里仅记录乘客确认，不代表系统验证了接驾完成。",
    # Deliberately weak control: no verified walking route is available.
    "direct_to_vehicle": "请从当前位置走到车辆旁边。",
}


def load_jsonl(path: Path) -> list[dict]:
    records, ids = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"].strip():
            raise ValueError(f"{path}:{number}: record needs a nonempty string id")
        if record["id"] in ids:
            raise ValueError(f"{path}:{number}: duplicate id {record['id']}")
        ids.add(record["id"])
        records.append(record)
    if not records:
        raise ValueError(f"{path}: no records")
    return records


def validate_cases(cases: list[dict]) -> None:
    for case in cases:
        label = case["id"]
        if case.get("source") != "authored_synthetic":
            raise ValueError(f"{label}: this demo accepts authored_synthetic cases only")
        turns = case.get("passenger_turns")
        if not isinstance(turns, list) or not turns or any(not isinstance(t, str) or not t.strip() for t in turns):
            raise ValueError(f"{label}: passenger_turns must contain nonempty strings")
        expected = case.get("expected", {})
        if not isinstance(expected, dict) or any(not isinstance(expected.get(key), str) for key in ("state", "action", "pickup_outcome")):
            raise ValueError(f"{label}: expected decision must contain strings")
        if (expected.get("state") not in STATES
                or expected.get("action") not in ACTIONS
                or expected.get("pickup_outcome") not in OUTCOMES):
            raise ValueError(f"{label}: invalid expected decision")
        if not isinstance(case.get("known"), list) or not isinstance(case.get("unknown"), list):
            raise ValueError(f"{label}: known/unknown lists required")
        if not isinstance(case.get("scenario"), str) or not isinstance(case.get("rationale"), str):
            raise ValueError(f"{label}: scenario and rationale required")


def predict(case_id: str, passenger_turns: list[str], variant: str) -> dict:
    """Receives only ID and passenger text, never expected labels/known facts."""
    if variant not in {"v1", "v2"}:
        raise ValueError(f"Unknown baseline {variant}")
    # v1's intentional flaws: concatenates old turns, positive substring before
    # negation, treats visibility as success, directs walking with no route.
    text = " ".join(passenger_turns) if variant == "v1" else passenger_turns[-1]
    outcome = "unconfirmed"
    if variant == "v1":
        if "找到" in text or "看到" in text:
            state, action, outcome = "vehicle_found", "verify_vehicle", "confirmed_by_passenger"
        elif "对面" in text:
            state, action = "opposite_side", "direct_to_vehicle"
        else:
            state, action = "not_found", "request_landmark"
    else:
        # This is a transparent hand-authored rule set, not language understanding.
        # Last-turn corrections take precedence in this bounded experiment.
        if any(word in text for word in ("可能", "好像", "不确定", "如果", "是不是")):
            state, action = "uncertain", "clarify_state"
        elif "已核对车辆并上车" in text:
            # Only the explicit standalone statement counts in this toy policy.
            # Negations, quotations and mixed claims stay unconfirmed.
            if re.fullmatch(r"(?:我)?已核对车辆并上车[。！! ]*", text.strip()):
                state, action, outcome = "onboarded", "acknowledge_onboarding", "confirmed_by_passenger"
            else:
                state, action = "uncertain", "clarify_state"
        elif any(word in text for word in ("没找到", "找不到", "看不到", "没看到", "还是没找到")):
            state, action = "not_found", "request_landmark"
        elif "不在对面" not in text and ("对面" in text or "另一侧" in text):
            state, action = "opposite_side", "clarify_safe_meeting"
        elif any(word in text for word in ("找到车", "看到车", "就在面前", "就在旁边")):
            state, action = "vehicle_found", "verify_vehicle"
        else:
            state, action = "uncertain", "clarify_state"
    reply = REPLIES[action]
    if variant == "v1" and outcome == "confirmed_by_passenger":
        reply = "接驾已完成。" + reply
    return {"id": case_id, "state": state, "action": action, "pickup_outcome": outcome, "reply": reply}


def schema_valid(prediction: dict) -> bool:
    return (set(prediction) == PREDICTION_FIELDS
            and isinstance(prediction.get("id"), str)
            and isinstance(prediction.get("state"), str) and prediction["state"] in STATES
            and isinstance(prediction.get("action"), str) and prediction["action"] in ACTIONS
            and isinstance(prediction.get("pickup_outcome"), str) and prediction["pickup_outcome"] in OUTCOMES
            and isinstance(prediction.get("reply"), str) and bool(prediction["reply"].strip()))


def grade(case: dict, prediction: dict) -> dict:
    expected = case["expected"]
    checks = {"schema": schema_valid(prediction)}
    for field in ("state", "action", "pickup_outcome"):
        checks[field] = checks["schema"] and prediction.get(field) == expected[field]
    # Bounded reply consistency: arbitrary free text is not semantically graded.
    # A different reply must be reviewed, even if the decision fields are right.
    checks["reply_template"] = checks["schema"] and prediction["reply"] == REPLIES[prediction["action"]]
    # Hard constraint for this dataset: no case provides a verified pedestrian
    # route. This is a decision-level check, NOT a general road safety verifier.
    checks["no_unverified_movement"] = checks["schema"] and prediction.get("action") != "direct_to_vehicle"
    # Tiny phrase tripwires are deliberately conservative: negated/quoted matches
    # also need review. Passing them does NOT prove arbitrary text is safe.
    reply = prediction.get("reply", "")
    checks["phrase_tripwires"] = checks["schema"] and not re.search(
        r"直接横穿马路|翻越护栏|直接穿过车流|我已经看到你|车辆就在你身后", reply)
    checks["no_unsupported_completion"] = checks["schema"] and (
        expected["pickup_outcome"] == "confirmed_by_passenger"
        or (prediction.get("pickup_outcome") == "unconfirmed"
            and not re.search(r"接驾已完成|已经成功上车", reply)))
    return {
        "id": case["id"], "scenario": case["scenario"], "source": case["source"],
        "passenger_turns": case["passenger_turns"], "known": case["known"], "unknown": case["unknown"],
        "expected": expected, "prediction": prediction, "checks": checks,
        "passed": all(checks.values()), "failed_checks": [key for key, ok in checks.items() if not ok],
        "rationale": case["rationale"],
    }


def evaluate(cases: list[dict], predictions: list[dict]) -> dict:
    if not cases:
        raise ValueError("No cases to evaluate")
    expected_ids = [case["id"] for case in cases]
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Duplicate case IDs")
    actual_ids = [prediction.get("id") for prediction in predictions]
    if len(set(actual_ids)) != len(actual_ids):
        raise ValueError("Duplicate prediction IDs")
    if set(expected_ids) != set(actual_ids):
        raise ValueError(f"Prediction IDs mismatch: missing={sorted(set(expected_ids)-set(actual_ids))}; "
                         f"extra={sorted(set(actual_ids)-set(expected_ids))}")
    by_id = {prediction["id"]: prediction for prediction in predictions}
    details = [grade(case, by_id[case["id"]]) for case in cases]
    total = len(details)
    passed = sum(detail["passed"] for detail in details)
    failures = Counter(key for detail in details for key in detail["failed_checks"])
    scenarios = sorted({case["scenario"] for case in cases})
    return {
        "total": total, "contract_passes": passed, "contract_pass_rate": round(passed / total, 4),
        "failed_checks": dict(sorted(failures.items())),
        "by_scenario": {scenario: {
            "total": sum(d["scenario"] == scenario for d in details),
            "contract_passes": sum(d["scenario"] == scenario and d["passed"] for d in details),
        } for scenario in scenarios},
        "details": details,
    }


def json_text(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def run(cases_path: Path, output_dir: Path, predictions_path: Path | None = None) -> dict:
    cases = load_jsonl(cases_path)
    validate_cases(cases)
    variants = {"external": load_jsonl(predictions_path)} if predictions_path else {
        variant: [predict(case["id"], case["passenger_turns"], variant) for case in cases]
        for variant in ("v1", "v2")
    }
    # Validate every run before writing anything, so a bad input cannot leave a
    # misleading partial report. Report content is deterministic, without timing.
    evaluations = {name: evaluate(cases, predictions) for name, predictions in variants.items()}
    report = {
        "experiment": "pickup-harness-v1", "data_source": "authored_synthetic",
        "case_file_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prediction_source": "external_file_unverified" if predictions_path else "deterministic_rules_no_model",
        "limitations": [
            "Visible diagnostic examples; rules were developed against these cases. No held-out generalization claim.",
            "Contract pass rate is not real pickup success, road safety certification, or model quality.",
            "Only passenger text is available; no real map, location, route, identity, boarding, or driver verification.",
            "Replies must match the built-in action template. A mismatch requires review, not a claim that the text is wrong.",
            "Phrase tripwires are incomplete and may flag negation/quotations. External replies require human review.",
        ],
        "runs": {},
    }
    if predictions_path:
        report["predictions_sha256"] = hashlib.sha256(predictions_path.read_bytes()).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, result in evaluations.items():
        (output_dir / f"details_{name}.json").write_text(json_text(result["details"]), encoding="utf-8")
        (output_dir / f"predictions_{name}.jsonl").write_text(
            "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in variants[name]), encoding="utf-8")
        report["runs"][name] = {key: value for key, value in result.items() if key != "details"}
    if not predictions_path:
        before = {detail["id"]: detail for detail in evaluations["v1"]["details"]}
        after = evaluations["v2"]["details"]
        report["comparison"] = {
            "fixed": [d["id"] for d in after if d["passed"] and not before[d["id"]]["passed"]],
            "regressed": [d["id"] for d in after if not d["passed"] and before[d["id"]]["passed"]],
            "still_failing": [d["id"] for d in after if not d["passed"] and not before[d["id"]]["passed"]],
        }
    (output_dir / "metrics.json").write_text(json_text(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    parser.add_argument("--predictions", type=Path, help="Grade external JSONL instead of running toy baselines")
    parser.add_argument("--fail-on-errors", action="store_true", help="Exit 1 if ANY evaluated contract fails (v1 intentionally fails)")
    args = parser.parse_args()
    try:
        report = run(args.cases, args.out, args.predictions)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    for name, result in report["runs"].items():
        print(f"{name}: {result['contract_passes']}/{result['total']} synthetic contract checks passed")
    print(f"Report: {args.out / 'metrics.json'}")
    print("Not real pickup success; no model was invoked by this harness.")
    return int(args.fail_on_errors and any(r["contract_passes"] < r["total"] for r in report["runs"].values()))


if __name__ == "__main__":
    sys.exit(main())
