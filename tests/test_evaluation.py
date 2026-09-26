"""
Unit and integration tests for evaluation metrics module (TASK-20).
"""

import json
from pathlib import Path
import unittest

from evaluate import (
    compute_f1,
    compute_macro_f1,
    compute_multilabel_f1,
    ScenarioEvaluator,
)
from src.config import load_reference_data
from src.core.state import SystemState


class TestEvaluationMetrics(unittest.TestCase):
    """Test core metric calculations."""

    def test_f1_calculation(self):
        # Perfect
        self.assertEqual(compute_f1(10, 0, 0), 1.0)
        # 50% precision/recall
        self.assertAlmostEqual(compute_f1(5, 5, 5), 0.5)
        # All zeros
        self.assertEqual(compute_f1(0, 0, 0), 1.0)

    def test_macro_f1(self):
        truth = ["OPEN", "OPEN", "CLOSED", "CONGESTED"]
        pred = ["OPEN", "OPEN", "CLOSED", "CONGESTED"]
        classes = ["OPEN", "CLOSED", "CONGESTED", "PARTIAL_BLOCK"]
        self.assertAlmostEqual(compute_macro_f1(truth, pred, classes), 1.0)

    def test_multilabel_f1(self):
        truth = [{"VISIBILITY"}, {"RAIN", "V2X"}]
        pred = [{"VISIBILITY"}, {"RAIN", "V2X"}]
        labels = ["VISIBILITY", "RAIN", "V2X", "GNSS"]
        self.assertAlmostEqual(compute_multilabel_f1(truth, pred, labels), 1.0)

    def test_evaluator_quick_run(self):
        ref = load_reference_data()
        evaluator = ScenarioEvaluator(scenario_id="TRAIN-001", ref=ref)
        state = SystemState(ref=ref)

        fixture_path = Path(__file__).parent / "fixtures/sample_packets.ndjson"
        decisions = []
        with open(fixture_path, "r", encoding="utf-8") as f:
            for line in f:
                pkt = json.loads(line.strip())
                dec = state.process_packet(pkt)
                decisions.append(dec)

        res = evaluator.evaluate(decisions)
        self.assertEqual(res["scenario_id"], "TRAIN-001")
        self.assertEqual(res["packets_evaluated"], len(decisions))
        self.assertEqual(res["safety_gate"]["critical_episodes_count"], 0)
        self.assertGreaterEqual(res["final_score"], 80.0)


if __name__ == "__main__":
    unittest.main()
