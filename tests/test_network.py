"""
Unit and integration tests for network topology, state estimation, and dynamic routing.
Covers TASK-05, TASK-06, TASK-07, and TASK-08.
"""

import json
import unittest
from pathlib import Path
import jsonschema

from src.config import load_reference_data
from src.network.graph import NetworkGraph
from src.network.estimator import SegmentStateEstimator
from src.network.router import CorridorRouter
from src.core.state import SystemState


class TestNetworkGraph(unittest.TestCase):
    """Test corridor topology, connectivity, constraints, and Dijkstra pathfinding."""

    def setUp(self):
        self.ref = load_reference_data()
        self.graph = NetworkGraph(self.ref)

    def test_graph_structure(self):
        self.assertEqual(len(self.graph.segments), 86)
        self.assertEqual(len(self.graph.nodes), 34)
        self.assertEqual(len(self.graph.hub_nodes), 4)
        self.assertEqual(len(self.ref.safe_stops), 10)

    def test_successors_connectivity(self):
        # S001 goes from M00 to M01. Successors must start at M01.
        succs = self.graph.get_successors("S001")
        self.assertTrue(len(succs) > 0)
        for s in succs:
            self.assertEqual(self.graph.segments[s]["from_node"], "M01")

    def test_shortest_path_after_current(self):
        # From S001 to HUB-02
        path = self.graph.find_shortest_path("S001", "HUB-02", after_current=True)
        self.assertIsNotNone(path)
        self.assertTrue(len(path) > 0)
        # First segment must start at S001's to_node (M01)
        self.assertEqual(self.graph.segments[path[0]]["from_node"], "M01")
        # Last segment must end at HUB-02's node (M07)
        self.assertEqual(self.graph.segments[path[-1]]["to_node"], "M07")

        # Contiguity check
        for i in range(len(path) - 1):
            cur_to = self.graph.segments[path[i]]["to_node"]
            nxt_from = self.graph.segments[path[i + 1]]["from_node"]
            self.assertEqual(cur_to, nxt_from)

    def test_tunnel_avoidance_for_odd_c(self):
        # S017 is a tunnel. For ODD-C, tunnels are forbidden.
        path_regular = self.graph.find_shortest_path("S015", "HUB-04", vehicle_mass_t=25.0)
        self.assertIn("S017", path_regular)

        path_odd_c = self.graph.find_shortest_path(
            "S015", "HUB-04", vehicle_mass_t=25.0, forbidden_structures={"tunnel"}
        )
        self.assertIsNotNone(path_odd_c)
        self.assertNotIn("S017", path_odd_c)
        # Route should take bypass S082
        self.assertEqual(path_odd_c[0], "S082")

    def test_nearest_safe_stop(self):
        # Look for safe stop from S001 with 30t vehicle
        stop_tuple = self.graph.find_nearest_safe_stop(
            current_segment_id="S001",
            vehicle_mass_t=30.0,
            safe_stop_occupancy={},
        )
        self.assertIsNotNone(stop_tuple)
        stop_id, stop_seg, stop_route = stop_tuple
        self.assertEqual(stop_id, "SS-01")
        self.assertEqual(stop_seg, "S005")

        # If SS-01 is full (capacity 4), should find next available safe stop
        stop_tuple_full = self.graph.find_nearest_safe_stop(
            current_segment_id="S001",
            vehicle_mass_t=30.0,
            safe_stop_occupancy={"SS-01": 4},
        )
        self.assertIsNotNone(stop_tuple_full)
        self.assertNotEqual(stop_tuple_full[0], "SS-01")


class TestSegmentStateEstimator(unittest.TestCase):
    """Test multi-source state fusion and classification rules."""

    def setUp(self):
        self.estimator = SegmentStateEstimator()

    def test_all_86_segments_reported(self):
        res = self.estimator.process_step(1, [])
        self.assertEqual(len(res), 86)
        ids = {r["segment_id"] for r in res}
        self.assertEqual(len(ids), 86)

    def test_classification_closed(self):
        event = {
            "event_type": "ROAD_OBSERVATION",
            "segment_id": "S017",
            "lane_count_open_estimate": 0,
            "lane_status": "CLOSED",
            "speed_kmh": 0.0,
        }
        res = self.estimator.process_step(1, [event])
        s17 = [r for r in res if r["segment_id"] == "S017"][0]
        self.assertEqual(s17["state"], "CLOSED")
        self.assertIn("STATE_CLOSED", s17["rationale_codes"])

    def test_classification_partial_block(self):
        event = {
            "event_type": "ROAD_OBSERVATION",
            "segment_id": "S017",
            "lane_count_open_estimate": 1,
            "lane_status": "PARTIAL_BLOCK",
            "speed_kmh": 18.0,
        }
        res = self.estimator.process_step(1, [event])
        s17 = [r for r in res if r["segment_id"] == "S017"][0]
        self.assertEqual(s17["state"], "PARTIAL_BLOCK")
        self.assertIn("STATE_PARTIAL_BLOCK", s17["rationale_codes"])

    def test_classification_congested(self):
        event = {
            "event_type": "ROAD_OBSERVATION",
            "segment_id": "S005",
            "lane_count_open_estimate": 3,
            "lane_status": "OPEN",
            "speed_kmh": 45.0,
            "occupancy_pct": 78.0,
        }
        res = self.estimator.process_step(1, [event])
        s5 = [r for r in res if r["segment_id"] == "S005"][0]
        self.assertEqual(s5["state"], "CONGESTED")
        self.assertIn("STATE_CONGESTED", s5["rationale_codes"])


class TestCorridorRouter(unittest.TestCase):
    """Test dynamic rerouting, action stability, and remote support limits."""

    def setUp(self):
        self.ref = load_reference_data()
        self.graph = NetworkGraph(self.ref)
        self.estimator = SegmentStateEstimator(self.ref)
        self.router = CorridorRouter(self.graph, self.ref)

        with open(Path(__file__).parent.parent / "data/contract/03_decision.schema.json") as f:
            self.schema = json.load(f)

    def test_reroute_on_closed_segment(self):
        # Step 1: normal state
        estimates_open = self.estimator.process_step(1, [])
        v_state = {
            "AV-001": {
                "is_active": True,
                "current_segment": "S015",
                "destination_hub_id": "HUB-04",
                "odd_profile_id": "ODD-A",
                "gross_mass_t": 27.0,
                "cargo_priority": 2,
            }
        }
        actions_1 = self.router.plan_vehicle_actions(1, estimates_open, v_state)
        a1 = [a for a in actions_1 if a["vehicle_id"] == "AV-001"][0]
        self.assertEqual(a1["motion_action"], "CONTINUE")

        # Step 2: S017 is closed -> REROUTE
        event_closed = {
            "event_type": "ROAD_OBSERVATION",
            "segment_id": "S017",
            "lane_count_open_estimate": 0,
            "lane_status": "CLOSED",
        }
        estimates_closed = self.estimator.process_step(2, [event_closed])
        actions_2 = self.router.plan_vehicle_actions(2, estimates_closed, v_state)
        a2 = [a for a in actions_2 if a["vehicle_id"] == "AV-001"][0]

        self.assertEqual(a2["motion_action"], "REROUTE")
        self.assertNotIn("S017", a2["route_segment_ids"])
        # Validate schema
        jsonschema.validate(a2, self.schema["properties"]["vehicle_actions"]["items"])

    def test_action_stability_no_flutter(self):
        # Step 1: initial
        estimates_open = self.estimator.process_step(1, [])
        v_state = {
            "AV-001": {
                "is_active": True,
                "current_segment": "S015",
                "destination_hub_id": "HUB-04",
                "odd_profile_id": "ODD-A",
                "gross_mass_t": 27.0,
                "cargo_priority": 2,
            }
        }
        self.router.plan_vehicle_actions(1, estimates_open, v_state)

        # Step 2: Reroute due to S017 closure
        event_closed = {
            "event_type": "ROAD_OBSERVATION",
            "segment_id": "S017",
            "lane_count_open_estimate": 0,
            "lane_status": "CLOSED",
        }
        estimates_closed = self.estimator.process_step(2, [event_closed])
        a2 = self.router.plan_vehicle_actions(2, estimates_closed, v_state)[0]
        self.assertEqual(a2["motion_action"], "REROUTE")

        # Step 3: Vehicle still on S015 -> must be CONTINUE, not repeated REROUTE
        a3 = self.router.plan_vehicle_actions(3, estimates_closed, v_state)[0]
        self.assertEqual(a3["motion_action"], "CONTINUE")

    def test_remote_support_pool_cap(self):
        # Force 10 vehicles to need remote support by placing them on trapped/isolated segments
        estimates_closed = self.estimator.process_step(1, [
            {"event_type": "ROAD_OBSERVATION", "segment_id": "S003", "lane_count_open_estimate": 0, "lane_status": "CLOSED"},
            {"event_type": "ROAD_OBSERVATION", "segment_id": "S002", "lane_count_open_estimate": 0, "lane_status": "CLOSED"},
        ])
        v_state = {}
        for i in range(1, 15):
            vid = f"AV-{i:03d}"
            v_state[vid] = {
                "is_active": True,
                "current_segment": "S001",
                "destination_hub_id": "HUB-02",
                "gross_mass_t": 25.0,
                "cargo_priority": (i % 5) + 1,
            }

        actions = self.router.plan_vehicle_actions(1, estimates_closed, v_state)
        remote_count = sum(1 for a in actions if a["remote_support_required"])
        self.assertLessEqual(remote_count, 6)


class TestFullSystemStateStage2(unittest.TestCase):
    """End-to-end test of SystemState with real packet processing."""

    def test_process_sample_packets(self):
        state = SystemState()
        with open(Path(__file__).parent / "fixtures/sample_packets.ndjson") as f:
            for line in f:
                pkt = json.loads(line)
                snapshot = state.process_packet(pkt)
                self.assertEqual(len(snapshot["state_estimates"]), 86)
                self.assertEqual(len(snapshot["source_assessments"]), 50)
                self.assertEqual(len(snapshot["vehicle_assessments"]), 72)
                self.assertEqual(len(snapshot["vehicle_actions"]), 72)


if __name__ == "__main__":
    unittest.main()
