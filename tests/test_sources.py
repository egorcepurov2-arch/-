"""
Unit and integration tests for Stage 4:
Infrastructure Source Health Tracking, Fault Detection, and Trust Scoring.
Covers TASK-13, TASK-14, TASK-15, and TASK-16.
"""

import json
import unittest
from pathlib import Path
import jsonschema

from src.config import load_reference_data
from src.sources.health import SourceHealthTracker
from src.core.state import SystemState


class TestSourceHealthTracker(unittest.TestCase):
    """Test health monitoring and fault detection for all 50 infrastructure sources."""

    def setUp(self):
        self.ref = load_reference_data()
        self.tracker = SourceHealthTracker(self.ref)

        with open(Path(__file__).parent.parent / "data/contract/03_decision.schema.json") as f:
            self.schema = json.load(f)

    def test_all_50_sources_reported(self):
        assessments = self.tracker.process_step(1, [])
        self.assertEqual(len(assessments), 50)
        sids = {a["source_id"] for a in assessments}
        self.assertEqual(len(sids), 50)
        # Validate against schema
        jsonschema.validate(assessments, self.schema["properties"]["source_assessments"])

    def test_fault_outage(self):
        # Trigger outage via NO_HEARTBEAT in INFRASTRUCTURE_HEALTH
        event = {
            "event_type": "INFRASTRUCTURE_HEALTH",
            "source_id": "RSU-06",
            "component_id": "RSU-06",
            "status": "NO_HEARTBEAT",
            "last_heartbeat_age_sec": 120.0,
        }
        assessments = self.tracker.process_step(1, [event])
        rsu6 = [a for a in assessments if a["source_id"] == "RSU-06"][0]
        self.assertEqual(rsu6["status"], "FAILED")
        self.assertEqual(rsu6["trust_score"], 0.0)
        self.assertIn("OUTAGE", rsu6["fault_types"])

    def test_fault_packet_loss(self):
        # Trigger packet loss via INFRASTRUCTURE_HEALTH
        event = {
            "event_type": "INFRASTRUCTURE_HEALTH",
            "source_id": "RSU-04",
            "component_id": "RSU-04",
            "status": "DEGRADED",
            "network_packet_loss_pct": 68.0,
        }
        assessments = self.tracker.process_step(1, [event])
        rsu4 = [a for a in assessments if a["source_id"] == "RSU-04"][0]
        self.assertEqual(rsu4["status"], "DEGRADED")
        self.assertIn("PACKET_LOSS", rsu4["fault_types"])
        self.assertLess(rsu4["trust_score"], 1.0)

    def test_fault_delivery_delay(self):
        # Trigger DELAY via 35-second gap between event_time and received_time
        event = {
            "event_type": "ROAD_OBSERVATION",
            "source_id": "DET-013",
            "segment_id": "S050",
            "event_time": "2026-09-28T09:21:00.000Z",
            "received_time": "2026-09-28T09:21:35.000Z",
            "speed_kmh": 85.0,
        }
        assessments = self.tracker.process_step(1, [event])
        det13 = [a for a in assessments if a["source_id"] == "DET-013"][0]
        self.assertEqual(det13["status"], "DEGRADED")
        self.assertIn("DELAY", det13["fault_types"])

    def test_fault_time_skew(self):
        # Trigger TIME_SKEW via clock offset > 1000ms
        event = {
            "event_type": "V2X_MESSAGE",
            "source_id": "RSU-05",
            "source_clock_offset_ms": 2500.0,
        }
        assessments = self.tracker.process_step(1, [event])
        rsu5 = [a for a in assessments if a["source_id"] == "RSU-05"][0]
        self.assertEqual(rsu5["status"], "DEGRADED")
        self.assertIn("TIME_SKEW", rsu5["fault_types"])

    def test_fault_stale_digital_twin(self):
        # Trigger STALE on DT-CORE via snapshot_age_sec >= 25s
        event = {
            "event_type": "DIGITAL_TWIN_SEGMENT",
            "source_id": "DT-CORE",
            "digital_twin_id": "DT-CORE",
            "segment_id": "S017",
            "snapshot_age_sec": 45.0,
        }
        assessments = self.tracker.process_step(1, [event])
        dt = [a for a in assessments if a["source_id"] == "DT-CORE"][0]
        self.assertEqual(dt["status"], "DEGRADED")
        self.assertIn("STALE", dt["fault_types"])

    def test_fault_freeze(self):
        # Send 7 events with identical float values to trigger FREEZE
        for step in range(1, 8):
            event = {
                "event_type": "ROAD_OBSERVATION",
                "source_id": "CAM-004",
                "segment_id": "S032",
                "speed_kmh": 72.34,
            }
            assessments = self.tracker.process_step(step, [event])

        cam4 = [a for a in assessments if a["source_id"] == "CAM-004"][0]
        self.assertIn("FREEZE", cam4["fault_types"])


class TestStage4FullIntegration(unittest.TestCase):
    """End-to-end integration test with SystemState processing sample packets."""

    def test_stage4_snapshot_compliance(self):
        state = SystemState()
        with open(Path(__file__).parent / "fixtures/sample_packets.ndjson") as f:
            for line in f:
                pkt = json.loads(line)
                snapshot = state.process_packet(pkt)
                self.assertEqual(len(snapshot["state_estimates"]), 86)
                self.assertEqual(len(snapshot["source_assessments"]), 50)
                self.assertEqual(len(snapshot["vehicle_assessments"]), 72)
                self.assertEqual(len(snapshot["vehicle_actions"]), 72)
                # Verify source assessments are populated
                for sa in snapshot["source_assessments"]:
                    self.assertIn(sa["status"], ["OK", "DEGRADED", "FAILED", "UNKNOWN"])
                    self.assertGreaterEqual(sa["trust_score"], 0.0)
                    self.assertLessEqual(sa["trust_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
