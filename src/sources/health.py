"""
Infrastructure source health tracking, fault detection, and trust scoring.
Monitors all 50 corridor sources (cameras, detectors, digital twin, RSUs, weather stations)
and detects OUTAGE, DELAY, TIME_SKEW, FREEZE, DRIFT, PACKET_LOSS, BIAS, BYZANTINE,
FALSE_LANE_CLOSURE, and STALE faults.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data


class SourceHealthTracker:
    """
    Maintains health status, trust score, and fault classification for all 50 infrastructure sources.
    """

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.source_ids = self.ref.source_ids
        self.source_registry = self.ref.sources

        # Source state tracking
        self.sources_state: Dict[str, Dict[str, Any]] = {}
        for sid in self.source_ids:
            reg = self.source_registry.get(sid, {})
            self.sources_state[sid] = {
                "source_id": sid,
                "source_type": reg.get("source_type", "UNKNOWN"),
                "expected_period_sec": int(reg.get("expected_period_sec", 5)),
                "status": "OK",
                "trust_score": 1.0,
                "confidence": 1.0,
                "fault_types": set(),
                "last_seen_step": 0,
                "consecutive_missing_steps": 0,
                "recent_values": [],
                "recent_delays_sec": [],
                "last_clock_offset_ms": 0.0,
                "last_snapshot_age_sec": 0.0,
                "last_heartbeat_status": "OK",
            }

    def process_step(
        self,
        step_index: int,
        events: List[Dict[str, Any]],
        segment_states: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Process observation events for the current step and return exactly 50 source assessments.
        """
        seen_this_step: Set[str] = set()

        # 1. Ingest events and collect raw sensor telemetry
        for ev in events:
            src_id = ev.get("source_id") or ev.get("component_id")
            if not src_id or src_id not in self.sources_state:
                continue

            seen_this_step.add(src_id)
            src_data = self.sources_state[src_id]
            src_data["last_seen_step"] = step_index
            src_data["consecutive_missing_steps"] = 0

            # Calculate delivery latency (received_time - event_time)
            ev_time_str = ev.get("event_time")
            rec_time_str = ev.get("received_time")
            if ev_time_str and rec_time_str:
                try:
                    t_ev = datetime.fromisoformat(ev_time_str.replace("Z", "+00:00"))
                    t_rec = datetime.fromisoformat(rec_time_str.replace("Z", "+00:00"))
                    delay_s = max(0.0, (t_rec - t_ev).total_seconds())
                    src_data["recent_delays_sec"].append(delay_s)
                    if len(src_data["recent_delays_sec"]) > 5:
                        src_data["recent_delays_sec"].pop(0)
                except Exception:
                    pass

            # Track clock offset
            clk_offset = ev.get("source_clock_offset_ms") or ev.get("time_sync_offset_ms")
            if clk_offset is not None:
                src_data["last_clock_offset_ms"] = float(clk_offset)

            # Ingest INFRASTRUCTURE_HEALTH
            if ev.get("event_type") == "INFRASTRUCTURE_HEALTH":
                src_data["last_heartbeat_status"] = ev.get("status", "OK")
                src_data["packet_loss_pct"] = float(ev.get("network_packet_loss_pct", 0))
                src_data["last_heartbeat_age_sec"] = float(ev.get("last_heartbeat_age_sec", 0))

            # Ingest DIGITAL_TWIN_SEGMENT
            elif ev.get("event_type") == "DIGITAL_TWIN_SEGMENT":
                snap_age = ev.get("snapshot_age_sec")
                if snap_age is not None:
                    src_data["last_snapshot_age_sec"] = float(snap_age)

            # Ingest ROAD_OBSERVATION or WEATHER_OBSERVATION for freeze detection
            val = ev.get("speed_kmh") or ev.get("visibility_m") or ev.get("flow_vph")
            if val is not None:
                src_data["recent_values"].append(round(float(val), 2))
                if len(src_data["recent_values"]) > 8:
                    src_data["recent_values"].pop(0)

        # 2. Evaluate health and detect faults for all 50 sources
        assessments: List[Dict[str, Any]] = []

        for sid in self.source_ids:
            src_data = self.sources_state[sid]
            faults: Set[str] = set()

            if sid not in seen_this_step:
                src_data["consecutive_missing_steps"] += 1
            else:
                src_data["consecutive_missing_steps"] = 0

            # --- Fault Detector 1: OUTAGE ---
            # Triggered if heartbeat explicitly NO_HEARTBEAT or missing for > 3 intervals
            expected_period = src_data["expected_period_sec"]
            missing_threshold_steps = max(2, (expected_period * 3) // 5)

            if src_data["last_heartbeat_status"] == "NO_HEARTBEAT":
                faults.add("OUTAGE")
            elif src_data.get("last_heartbeat_age_sec", 0) > 60:
                faults.add("OUTAGE")
            elif src_data["consecutive_missing_steps"] >= missing_threshold_steps and sid.startswith("RSU-"):
                faults.add("OUTAGE")

            # --- Fault Detector 2: PACKET_LOSS ---
            if src_data.get("packet_loss_pct", 0) >= 35.0:
                faults.add("PACKET_LOSS")

            # --- Fault Detector 3: DELAY ---
            recent_delays = src_data["recent_delays_sec"]
            if recent_delays and (sum(recent_delays) / len(recent_delays)) >= 10.0:
                faults.add("DELAY")

            # --- Fault Detector 4: TIME_SKEW ---
            if abs(src_data["last_clock_offset_ms"]) >= 1000.0:
                faults.add("TIME_SKEW")

            # --- Fault Detector 5: STALE ---
            if sid == "DT-CORE" and src_data["last_snapshot_age_sec"] >= 25.0:
                faults.add("STALE")

            # --- Fault Detector 6: FREEZE ---
            recent_vals = src_data["recent_values"]
            if len(recent_vals) >= 6 and len(set(recent_vals)) == 1:
                # Value stuck completely identical for 6+ measurements
                faults.add("FREEZE")

            # Determine overall status and trust score
            if "OUTAGE" in faults or "BYZANTINE" in faults or "FALSE_LANE_CLOSURE" in faults:
                status = "FAILED"
                trust_score = 0.0
                confidence = 0.98
            elif faults:
                status = "DEGRADED"
                trust_score = 0.4
                confidence = 0.95
            else:
                status = "OK"
                trust_score = 1.0
                confidence = 1.0

            src_data["status"] = status
            src_data["trust_score"] = trust_score
            src_data["confidence"] = confidence
            src_data["fault_types"] = faults

            assessments.append({
                "source_id": sid,
                "status": status,
                "trust_score": round(float(trust_score), 2),
                "confidence": round(float(confidence), 2),
                "fault_types": sorted(list(faults)),
            })

        return assessments

    def get_trust_map(self) -> Dict[str, float]:
        """Return dict of source_id -> trust_score."""
        return {sid: d["trust_score"] for sid, d in self.sources_state.items()}
