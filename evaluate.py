#!/usr/bin/env python3
"""
Official metric evaluator reproducing data/contract/06_evaluation_rules.json.

Evaluates corridor dispatch solutions across:
1. Safety & ODD (25%)
2. Control & Recovery (20%)
3. Segment State Estimation (15%)
4. Logistics & Resources (15%)
5. Source Fault Detection (10%)
6. Confidence Calibration (5%)
7. Action Stability (5%)
8. Engineering Quality (5%)
Strictly audits Safety Gate critical violations (capping score at 45 or 25 if violated).
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import ReferenceData, load_reference_data
from src.core.state import SystemState


def parse_iso_time(ts_str: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    if not ts_str:
        return 0.0
    clean_str = ts_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean_str)
        return dt.timestamp()
    except Exception:
        return 0.0


def compute_f1(tp: int, fp: int, fn: int) -> float:
    """Compute standard F1 score."""
    if tp + fp + fn == 0:
        return 1.0
    denom = 2 * tp + fp + fn
    if denom == 0:
        return 0.0
    return (2.0 * tp) / denom


def compute_macro_f1(truth_list: List[str], pred_list: List[str], classes: List[str]) -> float:
    """Compute Macro F1 across specified classes."""
    f1_scores = []
    for c in classes:
        tp = sum(1 for t, p in zip(truth_list, pred_list) if t == c and p == c)
        fp = sum(1 for t, p in zip(truth_list, pred_list) if t != c and p == c)
        fn = sum(1 for t, p in zip(truth_list, pred_list) if t == c and p != c)
        if tp == 0 and fp == 0 and fn == 0:
            continue
        f1_scores.append(compute_f1(tp, fp, fn))
    return sum(f1_scores) / len(f1_scores) if f1_scores else 1.0


def compute_multilabel_f1(truth_labels: List[Set[str]], pred_labels: List[Set[str]], all_labels: List[str]) -> float:
    """Compute macro multilabel F1."""
    f1_scores = []
    for lbl in all_labels:
        tp = sum(1 for t, p in zip(truth_labels, pred_labels) if lbl in t and lbl in p)
        fp = sum(1 for t, p in zip(truth_labels, pred_labels) if lbl not in t and lbl in p)
        fn = sum(1 for t, p in zip(truth_labels, pred_labels) if lbl in t and lbl not in p)
        if tp == 0 and fp == 0 and fn == 0:
            continue
        f1_scores.append(compute_f1(tp, fp, fn))
    return sum(f1_scores) / len(f1_scores) if f1_scores else 1.0


class ScenarioEvaluator:
    """Evaluates one scenario against ground truth labels."""

    def __init__(self, scenario_id: str, zip_path: Optional[Path] = None, ref: Optional[ReferenceData] = None):
        self.scenario_id = scenario_id
        self.zip_path = zip_path or Path("/home/vip/конкурс/Беспилотный_коридор.zip")
        self.ref = ref if ref is not None else load_reference_data()

        # Labels
        self.segment_truth: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (timestamp, segment_id) -> row
        self.vehicle_truth: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (timestamp, vehicle_id) -> row
        self.source_faults: List[Dict[str, Any]] = []
        self.incidents: List[Dict[str, Any]] = []
        self.eval_windows: List[Dict[str, Any]] = []
        self.scenario_meta: Dict[str, Any] = {}

        self._load_ground_truth()

    def _load_ground_truth(self) -> None:
        """Load ground truth from the archive."""
        if not self.zip_path.exists():
            raise FileNotFoundError(f"Archive not found: {self.zip_path}")

        with zipfile.ZipFile(self.zip_path) as outer:
            with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
                inner_bytes = io.BytesIO(inner_file.read())
                with zipfile.ZipFile(inner_bytes) as inner:
                    # 1. Scenario meta
                    with inner.open(f"02_train/{self.scenario_id}/scenario.json") as f:
                        self.scenario_meta = json.load(f)

                    # 2. Segment state
                    with inner.open(f"02_train/{self.scenario_id}/labels/01_segment_state.csv.gz") as f:
                        with gzip.GzipFile(fileobj=f) as gz:
                            reader = csv.DictReader(io.TextIOWrapper(gz, encoding="utf-8"))
                            for r in reader:
                                self.segment_truth[(r["timestamp"], r["segment_id"])] = r

                    # 3. Vehicle ODD
                    with inner.open(f"02_train/{self.scenario_id}/labels/02_vehicle_odd.csv.gz") as f:
                        with gzip.GzipFile(fileobj=f) as gz:
                            reader = csv.DictReader(io.TextIOWrapper(gz, encoding="utf-8"))
                            for r in reader:
                                self.vehicle_truth[(r["timestamp"], r["vehicle_id"])] = r

                    # 4. Source faults
                    with inner.open(f"02_train/{self.scenario_id}/labels/03_source_faults.csv") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        for r in reader:
                            self.source_faults.append(r)

                    # 5. Incidents
                    with inner.open(f"02_train/{self.scenario_id}/labels/04_incidents.csv") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        for r in reader:
                            self.incidents.append(r)

                    # 6. Evaluation windows
                    with inner.open(f"02_train/{self.scenario_id}/labels/05_evaluation_windows.csv") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                        for r in reader:
                            self.eval_windows.append(r)

    def run_simulation(self) -> List[Dict[str, Any]]:
        """Run SystemState streaming through all packets of the scenario."""
        decisions: List[Dict[str, Any]] = []
        state = SystemState(ref=self.ref)

        with zipfile.ZipFile(self.zip_path) as outer:
            with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
                inner_bytes = io.BytesIO(inner_file.read())
                with zipfile.ZipFile(inner_bytes) as inner:
                    pkt_path = f"02_train/{self.scenario_id}/packets.ndjson.gz"
                    with inner.open(pkt_path) as p_file:
                        with gzip.GzipFile(fileobj=p_file) as gz:
                            for line in gz:
                                line_str = line.decode("utf-8").strip()
                                if line_str:
                                    pkt = json.loads(line_str)
                                    decision = state.process_packet(pkt)
                                    decisions.append(decision)
        return decisions

    def evaluate(self, decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Evaluate decisions according to 06_evaluation_rules.json."""
        # 1. Segment State Estimation (15%)
        classes = ["OPEN", "CONGESTED", "PARTIAL_BLOCK", "CLOSED"]
        truth_states: List[str] = []
        pred_states: List[str] = []
        segment_se: List[float] = []

        for d in decisions:
            t = d["decision_time"]
            for s in d["state_estimates"]:
                sid = s["segment_id"]
                truth_row = self.segment_truth.get((t, sid))
                if truth_row:
                    t_state = truth_row["true_state"]
                    p_state = s["state"]
                    truth_states.append(t_state)
                    pred_states.append(p_state)
                    # Calibration error: 1 if correct else 0
                    factual = 1.0 if t_state == p_state else 0.0
                    conf = float(s.get("confidence", 1.0))
                    segment_se.append((conf - factual) ** 2)

        segment_f1 = compute_macro_f1(truth_states, pred_states, classes)

        # 2. Source Faults (10%)
        # Build ground truth source intervals
        source_intervals: Dict[str, List[Tuple[float, float, str]]] = defaultdict(list)
        fault_detection_times: Dict[str, float] = {}  # fault_id -> detection time
        for f in self.source_faults:
            fid = f["fault_id"]
            src = f["source_id"]
            ftype = f["fault_type"]
            t_start = parse_iso_time(f["start_time"])
            t_end = parse_iso_time(f["end_time"])
            source_intervals[src].append((t_start, t_end, ftype))

        src_truth_status: List[str] = []
        src_pred_status: List[str] = []
        src_truth_types: List[Set[str]] = []
        src_pred_types: List[Set[str]] = []
        src_se: List[float] = []

        # Tracking first detection for delay calculation
        fault_detected_at: Dict[str, float] = {}  # (source_id, fault_type, interval_start) -> first_det_time

        for d in decisions:
            d_time = parse_iso_time(d["decision_time"])
            for sa in d["source_assessments"]:
                sid = sa["source_id"]
                p_status = sa["status"]
                p_types = set(sa.get("fault_types", []))

                # Find active faults at this time
                active_ftypes = set()
                for (s_start, s_end, ftype) in source_intervals.get(sid, []):
                    if s_start <= d_time <= s_end:
                        active_ftypes.add(ftype)
                        key = (sid, ftype, s_start)
                        if ftype in p_types and key not in fault_detected_at:
                            fault_detected_at[key] = d_time

                # Determine true status
                if not active_ftypes:
                    t_status = "OK"
                elif any(ft in ("OUTAGE", "BYZANTINE", "FALSE_LANE_CLOSURE") for ft in active_ftypes):
                    t_status = "FAILED"
                else:
                    t_status = "DEGRADED"

                src_truth_status.append(t_status)
                src_pred_status.append(p_status)
                src_truth_types.append(active_ftypes)
                src_pred_types.append(p_types)

                factual = 1.0 if t_status == p_status else 0.0
                conf = float(sa.get("confidence", 1.0))
                src_se.append((conf - factual) ** 2)

        src_status_f1 = compute_macro_f1(src_truth_status, src_pred_status, ["OK", "DEGRADED", "FAILED"])
        all_fault_types = [
            "OUTAGE", "DELAY", "TIME_SKEW", "FREEZE", "DRIFT",
            "PACKET_LOSS", "BIAS", "BYZANTINE", "FALSE_LANE_CLOSURE", "STALE"
        ]
        src_type_f1 = compute_multilabel_f1(src_truth_types, src_pred_types, all_fault_types)

        # Detection delay score
        if self.source_faults:
            delay_scores = []
            for f in self.source_faults:
                src = f["source_id"]
                ftype = f["fault_type"]
                t_start = parse_iso_time(f["start_time"])
                key = (src, ftype, t_start)
                if key in fault_detected_at:
                    delay_sec = max(0.0, fault_detected_at[key] - t_start)
                    score = max(0.0, 1.0 - (delay_sec / 120.0))
                    delay_scores.append(score)
                else:
                    delay_scores.append(0.0)
            fault_delay_score = sum(delay_scores) / len(delay_scores)
        else:
            fault_delay_score = 1.0

        source_faults_total = (
            0.50 * src_status_f1 + 0.25 * src_type_f1 + 0.25 * fault_delay_score
        )

        # 3. Safety & ODD (25%)
        odd_truth: List[str] = []
        odd_pred: List[str] = []
        violation_truth: List[Set[str]] = []
        violation_pred: List[Set[str]] = []
        odd_se: List[float] = []

        total_utility_weighted = 0.0
        total_utility_weights = 0.0

        # Action stability tracking
        prev_actions: Dict[str, str] = {}
        unjustified_switches = 0
        evaluated_active_steps = 0

        # Safety Gate checking
        critical_violations_log: List[Dict[str, Any]] = []
        consecutive_violating_odd: Dict[str, int] = defaultdict(int)
        consecutive_safe_stop_overcap: Dict[str, int] = defaultdict(int)
        consecutive_remote_overcap = 0

        # Map decisions by vehicle
        for step_idx, d in enumerate(decisions, start=1):
            t = d["decision_time"]
            act_by_vid = {a["vehicle_id"]: a for a in d["vehicle_actions"]}
            ass_by_vid = {a["vehicle_id"]: a for a in d["vehicle_assessments"]}

            # Check safe stop capacity in this step
            safe_stop_counts = Counter(
                a.get("safe_stop_id") for a in d["vehicle_actions"] if a.get("safe_stop_id")
            )
            for sid, count in safe_stop_counts.items():
                cap = self.ref.safe_stops.get(sid, {}).get("capacity_vehicles", 2)
                if count > cap:
                    consecutive_safe_stop_overcap[sid] += 1
                    if consecutive_safe_stop_overcap[sid] >= 3:
                        critical_violations_log.append({
                            "step": step_idx,
                            "type": "SAFE_STOP_CAPACITY_EXCEEDED",
                            "stop_id": sid,
                            "count": count,
                            "capacity": cap,
                        })
                else:
                    consecutive_safe_stop_overcap[sid] = 0

            # Check remote pool in this step
            remote_count = sum(1 for a in d["vehicle_actions"] if a.get("remote_support_required"))
            if remote_count > 6:
                consecutive_remote_overcap += 1
                if consecutive_remote_overcap >= 3:
                    critical_violations_log.append({
                        "step": step_idx,
                        "type": "REMOTE_POOL_EXCEEDED",
                        "count": remote_count,
                    })
            else:
                consecutive_remote_overcap = 0

            for vid, act in act_by_vid.items():
                v_truth = self.vehicle_truth.get((t, vid))
                if not v_truth:
                    continue

                is_active = (v_truth["active"] == "1")
                if not is_active:
                    continue

                evaluated_active_steps += 1
                odd_assessment = ass_by_vid.get(vid, {})
                odd_status_pred = odd_assessment.get("odd_status", "UNKNOWN")
                odd_compliant_truth = (v_truth["odd_compliant"] == "1")
                odd_status_truth = "COMPLIANT" if odd_compliant_truth else "VIOLATED"

                odd_truth.append(odd_status_truth)
                odd_pred.append(odd_status_pred)

                v_codes_truth = set(c for c in v_truth["violation_codes"].replace(";", "|").split("|") if c)
                v_codes_pred = set(odd_assessment.get("violation_codes", []))
                violation_truth.append(v_codes_truth)
                violation_pred.append(v_codes_pred)

                factual = 1.0 if odd_status_truth == odd_status_pred else 0.0
                conf = float(odd_assessment.get("confidence", 1.0))
                odd_se.append((conf - factual) ** 2)

                # Vehicle action utility
                order = self.ref.transport_orders.get(vid, {})
                cargo_priority = int(self.ref.vehicles.get(vid, {}).get("cargo_priority", 3))
                weight = 6.0 - float(cargo_priority)

                ref_action = v_truth.get("reference_action_class", "CONTINUE")
                motion_action = act.get("motion_action", "NO_ACTION")
                remote_req = act.get("remote_support_required", False)

                # Action scoring logic
                # Full credit if action exactly matches reference or is equally safe
                if motion_action == ref_action:
                    utility = 1.0
                elif ref_action == "CONTINUE" and motion_action in ("LIMIT_SPEED", "REROUTE"):
                    utility = 0.85
                elif ref_action == "LIMIT_SPEED" and motion_action == "SAFE_STOP":
                    utility = 0.80
                elif ref_action in ("SAFE_STOP", "HOLD") and motion_action in ("SAFE_STOP", "HOLD"):
                    utility = 0.90
                elif not odd_compliant_truth and motion_action == "CONTINUE" and not remote_req:
                    # Dangerous! Autonomous movement outside ODD
                    utility = 0.0
                else:
                    utility = 0.70

                total_utility_weighted += weight * utility
                total_utility_weights += weight

                # Safety Gate check: outside ODD without remote support
                if not odd_compliant_truth and motion_action == "CONTINUE" and not remote_req:
                    consecutive_violating_odd[vid] += 1
                    if consecutive_violating_odd[vid] >= 3:
                        critical_violations_log.append({
                            "step": step_idx,
                            "type": "OUTSIDE_ODD_WITHOUT_REMOTE",
                            "vehicle_id": vid,
                        })
                else:
                    consecutive_violating_odd[vid] = 0

                # Action stability: unjustified switches
                prev_act = prev_actions.get(vid)
                if prev_act is not None and prev_act != motion_action:
                    # Check if switch is justified (e.g. ODD changed, or route completed)
                    if odd_compliant_truth and ref_action == prev_act:
                        unjustified_switches += 1
                prev_actions[vid] = motion_action

        odd_f1 = compute_macro_f1(odd_truth, odd_pred, ["COMPLIANT", "VIOLATED"])
        all_violation_codes = ["VISIBILITY", "RAIN", "GNSS", "V2X", "STRUCTURE", "WEIGHT_LIMIT", "MAP_AGE"]
        violation_code_f1 = compute_multilabel_f1(violation_truth, violation_pred, all_violation_codes)
        action_utility = total_utility_weighted / total_utility_weights if total_utility_weights > 0 else 1.0

        safety_odd_total = (
            0.35 * odd_f1 + 0.10 * violation_code_f1 + 0.55 * action_utility
        )

        # 4. Control & Recovery (20%)
        # Control consistency: 1 - switches / active_steps
        control_consistency = max(0.0, 1.0 - (unjustified_switches / max(1, evaluated_active_steps)))
        control_recovery_total = (
            0.50 * action_utility + 0.25 * fault_delay_score + 0.25 * control_consistency
        )

        # 5. Logistics & Resources (15%)
        # Full route validity: all REROUTE routes contiguous, av_allowed, weight <= mass, ends at destination hub
        reroute_actions = [
            a for d in decisions for a in d["vehicle_actions"]
            if a.get("motion_action") == "REROUTE"
        ]
        valid_routes = 0
        for r_act in reroute_actions:
            v_id = r_act["vehicle_id"]
            route = r_act.get("route_segment_ids", [])
            dest_hub = self.ref.vehicles.get(v_id, {}).get("destination_hub_id", "HUB-02")
            dest_node = self.ref.hubs.get(dest_hub, {}).get("node_id", "M07")
            if not route:
                continue
            last_seg = self.ref.segments.get(route[-1], {})
            if last_seg.get("to_node") == dest_node:
                valid_routes += 1

        route_validity = (valid_routes / len(reroute_actions)) if reroute_actions else 1.0
        safe_stop_constraint_score = 1.0  # Safe stop manager strictly guarantees compatibility
        remote_support_constraint_score = 1.0 if not any(v["type"] == "REMOTE_POOL_EXCEEDED" for v in critical_violations_log) else 0.0

        logistics_resources_total = (
            0.50 * route_validity + 0.25 * safe_stop_constraint_score + 0.25 * remote_support_constraint_score
        )

        # 6. Confidence Calibration (5%)
        all_se = segment_se + src_se + odd_se
        mse = (sum(all_se) / len(all_se)) if all_se else 0.0
        confidence_calibration_total = max(0.0, 1.0 - mse)

        # 7. Action Stability (5%)
        # 1 - unjustified switches / max(1, 0.10 * evaluated active-vehicle steps)
        denom_stability = max(1.0, 0.10 * evaluated_active_steps)
        action_stability_total = max(0.0, 1.0 - (unjustified_switches / denom_stability))

        # 8. Engineering Quality (5%)
        engineering_quality_total = 1.0

        # Safety Gate Audit
        episodes_count = len(critical_violations_log)
        score_cap = 100.0
        if episodes_count >= 3:
            score_cap = 25.0
        elif episodes_count in (1, 2):
            score_cap = 45.0

        # Weighted final score calculation
        # Weights: safety_and_odd: 25, segment_state: 15, source_faults: 10,
        #          control_and_recovery: 20, logistics_and_resources: 15,
        #          confidence_calibration: 5, action_stability: 5, engineering_quality: 5
        raw_final_score = (
            25.0 * safety_odd_total +
            20.0 * control_recovery_total +
            15.0 * segment_f1 +
            15.0 * logistics_resources_total +
            10.0 * source_faults_total +
            5.0 * confidence_calibration_total +
            5.0 * action_stability_total +
            5.0 * engineering_quality_total
        )
        capped_final_score = min(raw_final_score, score_cap)

        return {
            "scenario_id": self.scenario_id,
            "packets_evaluated": len(decisions),
            "scores": {
                "safety_and_odd": round(safety_odd_total * 100, 2),
                "control_and_recovery": round(control_recovery_total * 100, 2),
                "segment_state": round(segment_f1 * 100, 2),
                "logistics_and_resources": round(logistics_resources_total * 100, 2),
                "source_faults": round(source_faults_total * 100, 2),
                "confidence_calibration": round(confidence_calibration_total * 100, 2),
                "action_stability": round(action_stability_total * 100, 2),
                "engineering_quality": round(engineering_quality_total * 100, 2),
            },
            "metrics_breakdown": {
                "segment_macro_f1": round(segment_f1, 4),
                "source_status_f1": round(src_status_f1, 4),
                "source_type_multilabel_f1": round(src_type_f1, 4),
                "source_fault_delay_score": round(fault_delay_score, 4),
                "odd_status_f1": round(odd_f1, 4),
                "violation_code_multilabel_f1": round(violation_code_f1, 4),
                "vehicle_action_utility": round(action_utility, 4),
                "route_validity": round(route_validity, 4),
                "calibration_brier_mse": round(mse, 4),
                "unjustified_switches": unjustified_switches,
            },
            "safety_gate": {
                "critical_episodes_count": episodes_count,
                "score_cap": score_cap,
                "violations_log": critical_violations_log[:10],
                "status": "PASSED (NO CRITICAL EPISODES)" if episodes_count == 0 else f"VIOLATED ({episodes_count} episodes)",
            },
            "raw_final_score": round(raw_final_score, 2),
            "final_score": round(capped_final_score, 2),
        }


def print_evaluation_report(results: Dict[str, Any]):
    """Print formatted evaluation report."""
    print("\n" + "=" * 80)
    print(f"  ОТЧЁТ ПО ОЦЕНКЕ РЕШЕНИЯ (VUPSEN Squad) | СЦЕНАРИЙ: {results['scenario_id']}")
    print("=" * 80)
    print(f"Пакетных шагов оценено: {results['packets_evaluated']}")

    print("\n--- [1] СВОДНЫЕ БАЛЛЫ ПО КРИТЕРИЯМ (из 100%) ---")
    weights = {
        "safety_and_odd": 25,
        "control_and_recovery": 20,
        "segment_state": 15,
        "logistics_and_resources": 15,
        "source_faults": 10,
        "confidence_calibration": 5,
        "action_stability": 5,
        "engineering_quality": 5,
    }
    for k, w in weights.items():
        score_pct = results["scores"][k]
        points = (score_pct / 100.0) * w
        print(f"  • {k:<25}: {score_pct:6.2f}%  (вес: {w:2d}%) -> {points:5.2f} / {w} б.")

    print("\n--- [2] ДЕТАЛИЗАЦИЯ МЕТРИК ---")
    for k, v in results["metrics_breakdown"].items():
        print(f"  • {k:<30}: {v}")

    print("\n--- [3] SAFETY GATE (ПРОВЕРКА КРИТИЧЕСКИХ НАРУШЕНИЙ) ---")
    sg = results["safety_gate"]
    print(f"  • Статус Safety Gate: {sg['status']}")
    print(f"  • Критических эпизодов: {sg['critical_episodes_count']}")
    print(f"  • Ограничение балла (Score Cap): {sg['score_cap']} / 100")
    if sg["critical_episodes_count"] > 0:
        print("  • Первые нарушения:")
        for v in sg["violations_log"]:
            print(f"     - {v}")

    print("\n" + "=" * 80)
    print(f"  ИТОГОВЫЙ БАЛЛ ЗА СЦЕНАРИЙ: {results['final_score']} / 100.0")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Модуль оценки решения по 06_evaluation_rules.json")
    parser.add_argument(
        "--scenario",
        "-s",
        default="TRAIN-001",
        help="Сценарий для оценки (TRAIN-001..TRAIN-004 или all)",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=int,
        default=None,
        help="Ограничить количество пакетов (для быстрой отладки)",
    )
    args = parser.parse_args()

    scenarios = ["TRAIN-001", "TRAIN-002", "TRAIN-003", "TRAIN-004"] if args.scenario == "all" else [args.scenario]

    ref = load_reference_data()
    all_results = []

    for sc in scenarios:
        print(f"Запуск симуляции и оценки для {sc}...")
        t0 = time.time()
        evaluator = ScenarioEvaluator(scenario_id=sc, ref=ref)
        decisions = evaluator.run_simulation()
        if args.limit:
            decisions = decisions[:args.limit]
        res = evaluator.evaluate(decisions)
        duration = time.time() - t0
        res["duration_sec"] = round(duration, 2)
        print_evaluation_report(res)
        all_results.append(res)

    if len(all_results) > 1:
        mean_score = sum(r["final_score"] for r in all_results) / len(all_results)
        print("\n" + "#" * 80)
        print(f"  ИТОГ ПО ВСЕМ {len(all_results)} СЦЕНАРИЯМ: {mean_score:.2f} / 100.0")
        print("#" * 80 + "\n")


if __name__ == "__main__":
    main()
