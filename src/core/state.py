"""SystemState maintaining memory, telemetry tracking, and decision snapshot building."""

import sys
from typing import Any, Dict, List, Optional, Set

from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph
from src.network.estimator import SegmentStateEstimator
from src.network.router import CorridorRouter
from src.safety.odd_engine import ODDEngine
from src.safety.guard import SafetyGuard


class SystemState:
    """
    Maintains persistent memory between 5-second streaming observation packets.
    Tracks vehicle telemetry, sensor reliability, safe stops, and remote support pool.
    """

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()

        # Step and scenario info
        self.scenario_id: str = ""
        self.packet_id: str = ""
        self.step_index: int = 0
        self.decision_time: str = ""

        # Subsystems
        self.graph = NetworkGraph(self.ref)
        self.estimator = SegmentStateEstimator(self.ref)
        self.router = CorridorRouter(self.graph, self.ref)
        self.odd_engine = ODDEngine(self.ref)
        self.safety_guard = SafetyGuard(self.graph, self.ref)

        # Segment estimates cache
        self.latest_segment_estimates: List[Dict[str, Any]] = []

        # Vehicles state tracking
        self.vehicles_last_seen: Dict[str, Dict[str, Any]] = {}
        for vid in self.ref.vehicle_ids:
            v_catalog = self.ref.vehicles.get(vid, {})
            self.vehicles_last_seen[vid] = {
                "vehicle_id": vid,
                "current_segment": None,
                "speed_kmh": 0.0,
                "gnss_quality": 1.0,
                "map_age_min": 0.0,
                "odd_status": "COMPLIANT",
                "violation_codes": [],
                "motion_action": "CONTINUE",
                "destination_hub_id": v_catalog.get("destination_hub_id", "HUB-02"),
                "odd_profile_id": v_catalog.get("odd_profile_id", "ODD-A"),
                "gross_mass_t": v_catalog.get("gross_mass_t", 25.0),
                "cargo_priority": v_catalog.get("cargo_priority", 3),
                "is_active": True,
                "last_seen_step": 0,
                "last_event_time": None,
            }

        # Infrastructure sources state: source_id -> assessment dict
        self.source_assessments: Dict[str, Dict[str, Any]] = {}
        for src_id in self.ref.source_ids:
            self.source_assessments[src_id] = {
                "source_id": src_id,
                "status": "OK",
                "trust_score": 1.0,
                "confidence": 1.0,
                "fault_types": [],
            }

    def update_from_events(self, events: List[Dict[str, Any]]) -> None:
        """
        Process observation events in the current packet.
        Updates internal tracking for telemetry and sensor health.
        """
        # 1. Update segment state estimates via estimator
        self.latest_segment_estimates = self.estimator.process_step(
            step_index=self.step_index,
            events=events,
            source_trust={
                s: d["trust_score"] for s, d in self.source_assessments.items()
            },
        )

        # 2. Update environmental observations (weather & RSU)
        self.odd_engine.update_from_events(events)

        # 3. Track vehicle telemetry
        for ev in events:
            ev_type = ev.get("event_type")

            if ev_type == "VEHICLE_TELEMETRY":
                vid = ev.get("vehicle_id")
                if vid and vid in self.vehicles_last_seen:
                    v_state = self.vehicles_last_seen[vid]
                    v_state["current_segment"] = ev.get("segment_id")
                    v_state["speed_kmh"] = ev.get("speed_kmh", 0.0)
                    if ev.get("destination_hub_id"):
                        v_state["destination_hub_id"] = ev["destination_hub_id"]
                    if ev.get("cargo_priority"):
                        v_state["cargo_priority"] = ev["cargo_priority"]
                    if ev.get("odd_profile_id"):
                        v_state["odd_profile_id"] = ev["odd_profile_id"]
                    if ev.get("gnss_quality") is not None:
                        v_state["gnss_quality"] = ev["gnss_quality"]
                    if ev.get("map_age_min") is not None:
                        v_state["map_age_min"] = ev["map_age_min"]
                    v_state["last_event_time"] = ev.get("event_time")
                    v_state["is_active"] = True
                    v_state["last_seen_step"] = self.step_index

    def build_decision_snapshot(self) -> Dict[str, Any]:
        """
        Generate a full decision snapshot strictly conforming to 03_decision.schema.json.
        """
        # 1. State estimates for all 86 segments
        state_estimates = self.latest_segment_estimates
        if not state_estimates:
            state_estimates = self.estimator.process_step(self.step_index, [])

        blocked_segments = {
            item["segment_id"] for item in state_estimates if item["state"] == "CLOSED"
        }

        # 2. Source assessments for all 50 infrastructure sources
        source_assessments = []
        for src_id in self.ref.source_ids:
            src_info = self.source_assessments.get(src_id, {})
            source_assessments.append({
                "source_id": src_id,
                "status": src_info.get("status", "OK"),
                "trust_score": float(src_info.get("trust_score", 1.0)),
                "confidence": float(src_info.get("confidence", 1.0)),
                "fault_types": src_info.get("fault_types", []),
            })

        # 3. Vehicle assessments (ODD Engine) for all 72 vehicles
        vehicle_assessments = self.odd_engine.evaluate_all(self.vehicles_last_seen)

        # 4. Vehicle actions (Router + Safety Guard) for all 72 vehicles
        provisional_actions = self.router.plan_vehicle_actions(
            step_index=self.step_index,
            segment_estimates=state_estimates,
            vehicles_state=self.vehicles_last_seen,
        )

        final_actions = self.safety_guard.arbitrate_actions(
            step_index=self.step_index,
            provisional_actions=provisional_actions,
            odd_assessments=vehicle_assessments,
            vehicles_state=self.vehicles_last_seen,
            blocked_segments=blocked_segments,
        )

        snapshot = {
            "scenario_id": self.scenario_id,
            "packet_id": self.packet_id,
            "decision_time": self.decision_time,
            "state_estimates": state_estimates,
            "source_assessments": source_assessments,
            "vehicle_assessments": vehicle_assessments,
            "vehicle_actions": final_actions,
        }

        return snapshot

    def process_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single packet and return the decision snapshot.
        """
        self.scenario_id = packet.get("scenario_id", "")
        self.packet_id = packet.get("packet_id", "")
        self.step_index = packet.get("step", 0)
        self.decision_time = packet.get("decision_time", "")

        events = packet.get("events", [])
        self.update_from_events(events)

        return self.build_decision_snapshot()
