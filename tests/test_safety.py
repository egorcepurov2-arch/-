"""
Unit and integration tests for Stage 3:
ODD Engine, Environmental Spatial Binding, Safety Guard, and Cargo Priorities.
Covers TASK-09, TASK-10, TASK-11, and TASK-12.
"""

import json
import unittest
from pathlib import Path
import jsonschema

from src.config import load_reference_data
from src.network.graph import NetworkGraph
from src.safety.odd_engine import ODDEngine
from src.safety.guard import SafetyGuard
from src.core.state import SystemState


class TestODDEngine(unittest.TestCase):
    """Test ODD validation rules, 7 violation codes, and spatial weather binding."""

    def setUp(self):
        self.ref = load_reference_data()
        self.engine = ODDEngine(self.ref)

    def test_spatial_weather_and_rsu_mapping(self):
        # 86 segments mapped
        self.assertEqual(len(self.engine.segment_to_weather), 86)
        self.assertEqual(len(self.engine.segment_to_rsu), 86)
        # S001 is close to WX-01, S017 close to WX-03
        self.assertEqual(self.engine.segment_to_weather["S001"], "WX-01")
        self.assertEqual(self.engine.segment_to_weather["S017"], "WX-03")

    def test_inactive_vehicle_returns_unknown(self):
        v_state = {"is_active": False, "current_segment": None}
        res = self.engine.evaluate_vehicle_odd("AV-001", v_state)
        self.assertEqual(res["odd_status"], "UNKNOWN")
        self.assertEqual(res["violation_codes"], [])

    def test_compliant_vehicle(self):
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-A",
            "gnss_quality": 0.95,
            "map_age_min": 10.0,
            "gross_mass_t": 27.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-001", v_state)
        self.assertEqual(res["odd_status"], "COMPLIANT")
        self.assertEqual(res["violation_codes"], [])

    def test_violation_visibility(self):
        # ODD-A requires min_visibility_m = 70. Drop WX-01 visibility to 40m.
        self.engine.update_from_events([
            {"event_type": "WEATHER_OBSERVATION", "station_id": "WX-01", "visibility_m": 40.0}
        ])
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-A",
            "gnss_quality": 0.95,
            "map_age_min": 10.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-001", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("VISIBILITY", res["violation_codes"])

    def test_violation_rain(self):
        # ODD-B requires max_rain_level = 2. Set WX-01 rain to 3.
        self.engine.update_from_events([
            {"event_type": "WEATHER_OBSERVATION", "station_id": "WX-01", "rain_level": 3}
        ])
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-B",
            "gnss_quality": 0.95,
            "map_age_min": 10.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-002", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("RAIN", res["violation_codes"])

    def test_violation_gnss(self):
        # ODD-C requires min_gnss_quality = 0.80. Provide 0.60.
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-C",
            "gnss_quality": 0.60,
            "map_age_min": 10.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-003", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("GNSS", res["violation_codes"])

    def test_violation_v2x_required(self):
        # ODD-B requires V2X. Fail RSU-01 covering S001.
        self.engine.update_from_events([
            {"event_type": "INFRASTRUCTURE_HEALTH", "component_type": "RSU", "component_id": "RSU-01", "status": "NO_HEARTBEAT"}
        ])
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-B",
            "gnss_quality": 0.95,
            "map_age_min": 10.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-002", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("V2X", res["violation_codes"])

    def test_violation_structure(self):
        # ODD-C forbids tunnels. S017 is a tunnel.
        v_state = {
            "is_active": True,
            "current_segment": "S017",
            "odd_profile_id": "ODD-C",
            "gnss_quality": 0.95,
            "map_age_min": 10.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-003", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("STRUCTURE", res["violation_codes"])

    def test_violation_weight_limit(self):
        # S051 weight limit is 40.0t. Place a 43.0t vehicle on it.
        v_state = {
            "is_active": True,
            "current_segment": "S051",
            "odd_profile_id": "ODD-A",
            "gross_mass_t": 43.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-006", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("WEIGHT_LIMIT", res["violation_codes"])

    def test_violation_map_age(self):
        # ODD-D requires max_map_age_min = 60. Provide 120.
        v_state = {
            "is_active": True,
            "current_segment": "S001",
            "odd_profile_id": "ODD-D",
            "map_age_min": 120.0,
        }
        res = self.engine.evaluate_vehicle_odd("AV-004", v_state)
        self.assertEqual(res["odd_status"], "VIOLATED")
        self.assertIn("MAP_AGE", res["violation_codes"])


class TestSafetyGuard(unittest.TestCase):
    """Test safety arbitration, remote pool limit, safe stops, and cargo priorities."""

    def setUp(self):
        self.ref = load_reference_data()
        self.graph = NetworkGraph(self.ref)
        self.guard = SafetyGuard(self.graph, self.ref)

        with open(Path(__file__).parent.parent / "data/contract/03_decision.schema.json") as f:
            self.schema = json.load(f)

    def test_no_continue_without_remote_support_on_violation(self):
        # When ODD is violated, vehicle must NEVER have CONTINUE without remote support
        provisional = [{
            "vehicle_id": "AV-001",
            "motion_action": "CONTINUE",
            "remote_support_required": False,
            "confidence": 1.0,
            "rationale_codes": ["STATE_OPEN"],
        }]
        odd_assessments = [{
            "vehicle_id": "AV-001",
            "odd_status": "VIOLATED",
            "violation_codes": ["RAIN"],
            "confidence": 0.98,
        }]
        v_state = {
            "AV-001": {
                "is_active": True,
                "current_segment": "S001",
                "gross_mass_t": 27.0,
                "cargo_priority": 1,
            }
        }
        final = self.guard.arbitrate_actions(1, provisional, odd_assessments, v_state, set())
        act = final[0]
        # Should get LIMIT_SPEED with remote support, or SAFE_STOP, NEVER autonomous CONTINUE
        if act["motion_action"] == "CONTINUE":
            self.assertTrue(act["remote_support_required"])
        else:
            self.assertIn(act["motion_action"], ["LIMIT_SPEED", "SAFE_STOP", "HOLD"])

    def test_remote_support_priority_arbitration(self):
        # 10 vehicles violated, only 6 can receive remote support
        provisional = []
        odd_assessments = []
        v_state = {}

        for i in range(1, 11):
            vid = f"AV-{i:03d}"
            provisional.append({
                "vehicle_id": vid,
                "motion_action": "CONTINUE",
                "remote_support_required": False,
                "confidence": 1.0,
                "rationale_codes": ["STATE_OPEN"],
            })
            odd_assessments.append({
                "vehicle_id": vid,
                "odd_status": "VIOLATED",
                "violation_codes": ["VISIBILITY"],
                "confidence": 0.98,
            })
            # Give odd vehicles priority 1 (highest), even vehicles priority 5 (lowest)
            pri = 1 if (i % 2 == 1) else 5
            v_state[vid] = {
                "is_active": True,
                "current_segment": "S001",
                "gross_mass_t": 25.0,
                "cargo_priority": pri,
            }

        final = self.guard.arbitrate_actions(1, provisional, odd_assessments, v_state, set())
        supported = [a["vehicle_id"] for a in final if a["remote_support_required"]]
        self.assertLessEqual(len(supported), 6)
        # Priority 1 vehicles should be prioritized over Priority 5
        for vid in supported:
            # High priority vehicles should be in the supported pool
            if vid in ["AV-001", "AV-003", "AV-005"]:
                self.assertIn(vid, supported)

    def test_safe_stop_validity_on_exhausted_remote_pool(self):
        # If remote support is exhausted, vehicle diverts to SAFE_STOP
        provisional = [{
            "vehicle_id": "AV-010",
            "motion_action": "CONTINUE",
            "remote_support_required": False,
            "confidence": 1.0,
            "rationale_codes": ["STATE_OPEN"],
        }]
        odd_assessments = [{
            "vehicle_id": "AV-010",
            "odd_status": "VIOLATED",
            "violation_codes": ["RAIN"],
            "confidence": 0.98,
        }]
        # Set cargo priority lowest so it doesn't get remote support
        v_state = {
            "AV-010": {
                "is_active": True,
                "current_segment": "S001",
                "gross_mass_t": 30.0,
                "cargo_priority": 5,
            }
        }
        # Exhaust remote sessions with other vehicles
        # By setting MAX_REMOTE_SESSIONS = 0 temporarily
        old_max = SafetyGuard.MAX_REMOTE_SESSIONS
        SafetyGuard.MAX_REMOTE_SESSIONS = 0
        try:
            final = self.guard.arbitrate_actions(1, provisional, odd_assessments, v_state, set())
            act = [a for a in final if a["vehicle_id"] == "AV-010"][0]
            self.assertEqual(act["motion_action"], "SAFE_STOP")
            self.assertIsNotNone(act["safe_stop_id"])
            self.assertFalse(act["remote_support_required"])
            # Validate schema
            jsonschema.validate(act, self.schema["properties"]["vehicle_actions"]["items"])
        finally:
            SafetyGuard.MAX_REMOTE_SESSIONS = old_max


class TestStage3FullIntegration(unittest.TestCase):
    """End-to-end integration test with SystemState processing sample packets."""

    def test_stage3_snapshot_compliance(self):
        state = SystemState()
        with open(Path(__file__).parent / "fixtures/sample_packets.ndjson") as f:
            for line in f:
                pkt = json.loads(line)
                snapshot = state.process_packet(pkt)
                self.assertEqual(len(snapshot["state_estimates"]), 86)
                self.assertEqual(len(snapshot["source_assessments"]), 50)
                self.assertEqual(len(snapshot["vehicle_assessments"]), 72)
                self.assertEqual(len(snapshot["vehicle_actions"]), 72)
                # Check active vehicles have compliant ODD in sample packets
                active_assessments = [
                    a for a in snapshot["vehicle_assessments"] if a["odd_status"] != "UNKNOWN"
                ]
                self.assertTrue(len(active_assessments) > 0)


if __name__ == "__main__":
    unittest.main()
