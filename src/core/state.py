"""SystemState maintaining memory, telemetry tracking, and decision snapshot building."""

import sys
from typing import Any, Dict, List, Optional, Set

from src.config import ReferenceData, load_reference_data


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

        # Resource managers
        # Remote support: strictly <= 6 concurrent sessions
        self.active_remote_sessions: Set[str] = set()

        # Safe stops: safe_stop_id -> occupied count
        self.safe_stop_occupancy: Dict[str, int] = {
            stop_id: 0 for stop_id in self.ref.safe_stops
        }

        # Vehicles state tracking
        self.vehicles_last_seen: Dict[str, Dict[str, Any]] = {}
        for vid in self.ref.vehicle_ids:
            self.vehicles_last_seen[vid] = {
                "vehicle_id": vid,
                "current_segment": None,
                "speed_kmh": 0.0,
                "odd_status": "COMPLIANT",
                "violation_codes": [],
                "motion_action": "CONTINUE",
                "remote_support": False,
                "last_event_time": None,
            }

        # Segment states: segment_id -> state dict
        self.segment_states: Dict[str, Dict[str, Any]] = {}
        for sid in self.ref.segment_ids:
            self.segment_states[sid] = {
                "state": "OPEN",
                "confidence": 1.0,
                "rationale_codes": ["STATE_OPEN"],
            }

        # Infrastructure sources state: source_id -> assessment dict
        self.source_assessments: Dict[str, Dict[str, Any]] = {}
        for src_id in self.ref.source_ids:
            self.source_assessments[src_id] = {
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
        for ev in events:
            ev_type = ev.get("event_type")
            source_id = ev.get("source_id")

            # Track vehicle telemetry
            if ev_type == "VEHICLE_TELEMETRY":
                vid = ev.get("vehicle_id")
                if vid and vid in self.vehicles_last_seen:
                    v_state = self.vehicles_last_seen[vid]
                    v_state["current_segment"] = ev.get("segment_id")
                    v_state["speed_kmh"] = ev.get("speed_kmh", 0.0)
                    v_state["last_event_time"] = ev.get("event_time")

    def build_decision_snapshot(self) -> Dict[str, Any]:
        """
        Generate a full decision snapshot strictly conforming to 03_decision.schema.json.
        """
        # 1. State estimates for all 86 segments
        state_estimates = []
        for sid in self.ref.segment_ids:
            s_info = self.segment_states.get(sid, {})
            state_estimates.append({
                "segment_id": sid,
                "state": s_info.get("state", "OPEN"),
                "confidence": float(s_info.get("confidence", 1.0)),
                "rationale_codes": s_info.get("rationale_codes", ["STATE_OPEN"]),
            })

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

        # 3. Vehicle assessments (ODD) for all 72 vehicles
        vehicle_assessments = []
        for vid in self.ref.vehicle_ids:
            v_info = self.vehicles_last_seen.get(vid, {})
            vehicle_assessments.append({
                "vehicle_id": vid,
                "odd_status": v_info.get("odd_status", "COMPLIANT"),
                "violation_codes": v_info.get("violation_codes", []),
                "confidence": 1.0,
            })

        # 4. Vehicle actions for all 72 vehicles
        vehicle_actions = []
        for vid in self.ref.vehicle_ids:
            v_info = self.vehicles_last_seen.get(vid, {})
            action = v_info.get("motion_action", "CONTINUE")
            vehicle_actions.append({
                "vehicle_id": vid,
                "motion_action": action,
                "remote_support_required": bool(v_info.get("remote_support", False)),
                "confidence": 1.0,
                "rationale_codes": ["STATE_OPEN"],
            })

        snapshot = {
            "scenario_id": self.scenario_id,
            "packet_id": self.packet_id,
            "decision_time": self.decision_time,
            "state_estimates": state_estimates,
            "source_assessments": source_assessments,
            "vehicle_assessments": vehicle_assessments,
            "vehicle_actions": vehicle_actions,
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
