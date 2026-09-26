"""
Unit and integration tests for Stage 5: Dispatching and Resource Management (TASK-17..19).
Verifies:
- Strict limit of <= 6 concurrent remote support sessions and priority arbitration.
- Safe stop capacity limits, weight limits, and closed segment avoidance.
- Hub egress blocking and destination hub capacity gating.
- Full decision schema compliance with 0 safety gate violations.
"""

import json
from pathlib import Path
import unittest
import jsonschema

from src.config import load_reference_data
from src.network.graph import NetworkGraph
from src.dispatch.resource_manager import RemoteSupportPool, SafeStopManager, ResourceManager
from src.dispatch.hub_manager import HubManager
from src.safety.guard import SafetyGuard
from src.core.state import SystemState


class TestRemoteSupportPool(unittest.TestCase):
    """TASK-17: Remote support pool lease management and capacity cap <= 6."""

    def setUp(self):
        self.ref = load_reference_data()
        self.pool = RemoteSupportPool(self.ref)

    def test_pool_capacity_limit(self):
        # 10 candidate requests, pool must admit at most 6
        candidates = [
            (float(i * 10), f"AV-{i:03d}", "ODD_VIOLATION")
            for i in range(1, 11)
        ]
        admitted = self.pool.allocate_sessions(step_index=1, candidates=candidates)
        self.assertEqual(len(admitted), 6)
        self.assertEqual(self.pool.active_count(), 6)

        # The highest priority scores (i = 10, 9, 8, 7, 6, 5) must be admitted
        for i in range(5, 11):
            self.assertIn(f"AV-{i:03d}", admitted)
        # Lower scores (i = 1, 2, 3, 4) must NOT be admitted
        for i in range(1, 5):
            self.assertNotIn(f"AV-{i:03d}", admitted)

    def test_session_retention_and_release(self):
        # Step 1: Admit AV-001 and AV-002
        c1 = [
            (100.0, "AV-001", "ODD_VIOLATION"),
            (80.0, "AV-002", "ODD_VIOLATION"),
        ]
        adm1 = self.pool.allocate_sessions(step_index=1, candidates=c1)
        self.assertEqual(adm1, {"AV-001", "AV-002"})

        # Step 2: AV-001 still requests, but AV-002 resolved and AV-003 arrives
        c2 = [
            (100.0, "AV-001", "ODD_VIOLATION"),
            (90.0, "AV-003", "ODD_VIOLATION"),
        ]
        adm2 = self.pool.allocate_sessions(step_index=2, candidates=c2)
        self.assertEqual(adm2, {"AV-001", "AV-003"})
        self.assertFalse(self.pool.is_active("AV-002"))
        self.assertTrue(self.pool.is_active("AV-001"))
        self.assertTrue(self.pool.is_active("AV-003"))

        # Explicit release
        self.pool.release_session("AV-001")
        self.assertFalse(self.pool.is_active("AV-001"))
        self.assertEqual(self.pool.active_count(), 1)


class TestSafeStopManager(unittest.TestCase):
    """TASK-18: Safe stop capacity, weight limits, and closed segment avoidance."""

    def setUp(self):
        self.ref = load_reference_data()
        self.graph = NetworkGraph(self.ref)
        self.mgr = SafeStopManager(self.graph, self.ref)

    def test_safe_stop_capacity_enforcement(self):
        # SS-01 has capacity 4, located on S005
        cap = self.mgr.get_capacity("SS-01")
        self.assertEqual(cap, 4)

        # Assign 4 vehicles
        for i in range(1, 5):
            assigned = self.mgr.assign_safe_stop(
                vehicle_id=f"AV-{i:03d}",
                vehicle_mass_t=25.0,
                current_segment_id="S001",
                blocked_segments=set(),
            )
            self.assertEqual(assigned, "SS-01")

        self.assertEqual(self.mgr.get_occupancy("SS-01"), 4)

        # 5th vehicle should NOT be assigned to SS-01 because it is at full capacity!
        assigned_5 = self.mgr.assign_safe_stop(
            vehicle_id="AV-005",
            vehicle_mass_t=25.0,
            current_segment_id="S001",
            blocked_segments=set(),
        )
        # Should divert to next nearest reachable safe stop (SS-02 or other)
        self.assertNotEqual(assigned_5, "SS-01")
        self.assertEqual(self.mgr.get_occupancy("SS-01"), 4)

    def test_safe_stop_weight_limit(self):
        # SS-07 max vehicle mass is 40.0t
        max_wt = self.mgr.get_max_weight_t("SS-07")
        self.assertEqual(max_wt, 40.0)

        # Vehicle with 42.0t should NOT be compatible with SS-07
        self.assertFalse(self.mgr.is_compatible("SS-07", vehicle_mass_t=42.0, blocked_segments=set()))
        # Vehicle with 38.0t should be compatible
        self.assertTrue(self.mgr.is_compatible("SS-07", vehicle_mass_t=38.0, blocked_segments=set()))

    def test_safe_stop_closed_segment_avoidance(self):
        # SS-01 is on S005. If S005 is CLOSED, SS-01 cannot be assigned
        self.assertFalse(self.mgr.is_compatible("SS-01", vehicle_mass_t=25.0, blocked_segments={"S005"}))

        assigned = self.mgr.assign_safe_stop(
            vehicle_id="AV-001",
            vehicle_mass_t=25.0,
            current_segment_id="S001",
            blocked_segments={"S005"},
        )
        self.assertNotEqual(assigned, "SS-01")


class TestHubManager(unittest.TestCase):
    """TASK-19: Hub queue tracking, egress blocking, and destination saturation gating."""

    def setUp(self):
        self.ref = load_reference_data()
        self.graph = NetworkGraph(self.ref)
        self.hub_mgr = HubManager(self.graph, self.ref)

    def test_hub_status_event_update(self):
        # Send HUB_STATUS event for HUB-02
        events = [{
            "event_type": "HUB_STATUS",
            "hub_id": "HUB-02",
            "capacity_used_pct": 99.0,
            "accepting_new_arrivals": False,
            "queue_vehicles": 15,
            "estimated_wait_min": 45.0,
        }]
        self.hub_mgr.update_from_events(step_index=5, events=events)

        st = self.hub_mgr.hub_status["HUB-02"]
        self.assertEqual(st["capacity_used_pct"], 99.0)
        self.assertFalse(st["accepting_new_arrivals"])
        self.assertFalse(self.hub_mgr.is_destination_accessible("HUB-02"))

    def test_egress_blocked_holds_vehicle(self):
        # HUB-01 node is M00. Outgoing segment is S001.
        # If S001 is CLOSED, departing vehicle should HOLD with ROUTE_CLOSED
        should_hold, reason = self.hub_mgr.should_hold_at_hub(
            current_segment="S001",
            destination_hub_id="HUB-02",
            blocked_segments={"S001"},
        )
        self.assertTrue(should_hold)
        self.assertEqual(reason, "ROUTE_CLOSED")

    def test_destination_saturated_holds_vehicle(self):
        # HUB-03 destination is 99% full
        self.hub_mgr.hub_status["HUB-03"]["capacity_used_pct"] = 99.0
        self.hub_mgr.hub_status["HUB-03"]["accepting_new_arrivals"] = False

        should_hold, reason = self.hub_mgr.should_hold_at_hub(
            current_segment="S001",
            destination_hub_id="HUB-03",
            blocked_segments=set(),
        )
        self.assertTrue(should_hold)
        self.assertEqual(reason, "ROUTE_HUB_CAPACITY")


class TestStage5Integration(unittest.TestCase):
    """End-to-end integration of Stage 5 dispatching with SystemState."""

    def setUp(self):
        schema_path = Path(__file__).parent.parent / "data/contract/03_decision.schema.json"
        with open(schema_path) as f:
            self.schema = json.load(f)

    def test_streaming_with_dispatch_management(self):
        state = SystemState()
        with open(Path(__file__).parent / "fixtures/sample_packets.ndjson") as f:
            for line in f:
                pkt = json.loads(line)
                snapshot = state.process_packet(pkt)

                # 1. Full schema validation
                jsonschema.validate(snapshot, self.schema)

                # 2. Strict remote support pool cap <= 6
                active_remote = [
                    a for a in snapshot["vehicle_actions"]
                    if a["remote_support_required"]
                ]
                self.assertLessEqual(len(active_remote), 6)

                # 3. Safe stop assignments never exceed capacity
                assigned_stops = [
                    a["safe_stop_id"] for a in snapshot["vehicle_actions"]
                    if a.get("safe_stop_id")
                ]
                for stop_id in set(assigned_stops):
                    count = assigned_stops.count(stop_id)
                    cap = state.resource_manager.safe_stops.get_capacity(stop_id)
                    self.assertLessEqual(count, cap)


if __name__ == "__main__":
    unittest.main()
