"""
Segment state estimation module for the autonomous vehicle corridor.
Fuses physical roadside detector data, video analytics, digital twin,
and vehicle telemetry to estimate states for all 86 corridor segments.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data


class SegmentStateEstimator:
    """
    Estimates the traffic state and confidence for all 86 corridor segments.
    States: OPEN, CONGESTED, PARTIAL_BLOCK, CLOSED, UNKNOWN.
    """

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.segment_ids = self.ref.segment_ids
        self.segments = self.ref.segments

        # Segment memory: segment_id -> dict of persistent status
        self.segment_memory: Dict[str, Dict[str, Any]] = {}
        for sid in self.segment_ids:
            self.segment_memory[sid] = {
                "state": "OPEN",
                "confidence": 1.0,
                "rationale_codes": ["STATE_OPEN"],
                "last_speed_kmh": self.segments[sid]["speed_limit_kmh"],
                "last_occupancy_pct": 30.0,
                "last_lanes_open": self.segments[sid]["lanes"],
                "last_updated_step": 0,
            }

    def process_step(
        self,
        step_index: int,
        events: List[Dict[str, Any]],
        source_trust: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Process incoming observation events for the current step
        and return the full list of 86 segment state estimates.
        """
        if source_trust is None:
            source_trust = {}

        # 1. Group observations by segment_id
        road_obs: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in self.segment_ids}
        twin_obs: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid in self.segment_ids}
        telemetry_speeds: Dict[str, List[float]] = {sid: [] for sid in self.segment_ids}

        for ev in events:
            ev_type = ev.get("event_type")
            sid = ev.get("segment_id")
            if not sid or sid not in self.segment_ids:
                continue

            if ev_type == "ROAD_OBSERVATION":
                road_obs[sid].append(ev)
            elif ev_type == "DIGITAL_TWIN_SEGMENT":
                twin_obs[sid].append(ev)
            elif ev_type == "V2X_MESSAGE" and ev.get("message_type") == "ROAD_STATE":
                road_obs[sid].append(ev)
            elif ev_type == "VEHICLE_TELEMETRY":
                spd = ev.get("speed_kmh")
                if spd is not None:
                    telemetry_speeds[sid].append(spd)

        # 2. Estimate state for each of the 86 segments
        estimates: List[Dict[str, Any]] = []

        for sid in self.segment_ids:
            seg_info = self.segments[sid]
            total_lanes = seg_info["lanes"]
            speed_limit = seg_info["speed_limit_kmh"]
            mem = self.segment_memory[sid]

            s_road = road_obs[sid]
            s_twin = twin_obs[sid]
            s_telemetry = telemetry_speeds[sid]

            has_fresh_data = bool(s_road or s_twin or s_telemetry)

            if has_fresh_data:
                state, conf, rationales, lanes_open, speed, occ = self._evaluate_segment(
                    sid=sid,
                    total_lanes=total_lanes,
                    speed_limit=speed_limit,
                    road_events=s_road,
                    twin_events=s_twin,
                    telemetry_speeds=s_telemetry,
                    source_trust=source_trust,
                )

                # Update memory
                mem["state"] = state
                mem["confidence"] = conf
                mem["rationale_codes"] = rationales
                mem["last_lanes_open"] = lanes_open
                mem["last_speed_kmh"] = speed
                mem["last_occupancy_pct"] = occ
                mem["last_updated_step"] = step_index
            else:
                # No fresh data in this packet: decay confidence
                age_steps = step_index - mem["last_updated_step"]
                state = mem["state"]
                rationales = list(mem["rationale_codes"])

                if age_steps > 12:  # > 60 seconds without data
                    if "NO_FRESH_DATA" not in rationales:
                        rationales.append("NO_FRESH_DATA")
                    conf = max(0.5, mem["confidence"] * 0.95)
                else:
                    conf = mem["confidence"]

            estimates.append({
                "segment_id": sid,
                "state": state,
                "confidence": round(float(conf), 3),
                "rationale_codes": rationales,
            })

        return estimates

    def _evaluate_segment(
        self,
        sid: str,
        total_lanes: int,
        speed_limit: float,
        road_events: List[Dict[str, Any]],
        twin_events: List[Dict[str, Any]],
        telemetry_speeds: List[float],
        source_trust: Dict[str, float],
    ) -> Tuple[str, float, List[str], int, float, float]:
        """
        Synthesize multi-source observations for a single segment.
        Returns: (state, confidence, rationale_codes, lanes_open, avg_speed, avg_occupancy)
        """
        # Determine lane closure state
        # Road observations from physical detectors/cameras have highest precedence
        lanes_open = total_lanes
        has_closure_signal = False
        detector_reported_closed = False
        detector_reported_partial = False

        speeds: List[float] = []
        occupancies: List[float] = []
        queues: List[float] = []

        for ev in road_events:
            src_id = ev.get("source_id", "")
            trust = source_trust.get(src_id, 1.0)
            if trust < 0.3:
                # Downweight or skip untrusted / byzantine sources
                continue

            # Check open lanes count
            lane_open_est = ev.get("lane_count_open_estimate")
            if lane_open_est is not None:
                has_closure_signal = True
                lanes_open = min(lanes_open, lane_open_est)
                if lane_open_est == 0:
                    detector_reported_closed = True
                elif lane_open_est < total_lanes:
                    detector_reported_partial = True

            lane_status = ev.get("lane_status")
            if lane_status == "CLOSED":
                detector_reported_closed = True
                lanes_open = 0
            elif lane_status == "PARTIAL_BLOCK":
                detector_reported_partial = True
                if lanes_open == total_lanes:
                    lanes_open = max(1, total_lanes - 1)

            if ev.get("speed_kmh") is not None:
                speeds.append(ev["speed_kmh"])
            elif ev.get("advisory_speed_kmh") is not None:
                speeds.append(ev["advisory_speed_kmh"])
            if ev.get("occupancy_pct") is not None:
                occupancies.append(ev["occupancy_pct"])
            if ev.get("queue_estimate_m") is not None:
                queues.append(float(ev["queue_estimate_m"]))

        # Fallback to digital twin if no trusted road detector reported closures
        if not has_closure_signal and twin_events:
            for dt_ev in twin_events:
                dt_open = dt_ev.get("lane_count_open")
                dt_closure = dt_ev.get("closure_state")
                if dt_open is not None:
                    lanes_open = min(lanes_open, dt_open)
                if dt_closure == "CLOSED" or dt_open == 0:
                    detector_reported_closed = True
                    lanes_open = 0
                elif dt_closure == "PARTIAL_BLOCK" or (dt_open is not None and dt_open < total_lanes):
                    detector_reported_partial = True
                if dt_ev.get("speed_kmh") is not None:
                    speeds.append(dt_ev["speed_kmh"])

        # Incorporate vehicle telemetry speeds
        speeds.extend(telemetry_speeds)

        avg_speed = sum(speeds) / len(speeds) if speeds else speed_limit
        avg_occ = sum(occupancies) / len(occupancies) if occupancies else 30.0
        max_queue = max(queues) if queues else 0.0

        # Classification logic strictly matching ground truth definitions
        if detector_reported_closed or lanes_open == 0:
            return "CLOSED", 0.99, ["STATE_CLOSED"], 0, avg_speed, avg_occ

        if detector_reported_partial or lanes_open < total_lanes:
            return "PARTIAL_BLOCK", 0.98, ["STATE_PARTIAL_BLOCK"], lanes_open, avg_speed, avg_occ

        # When all lanes are physically open, evaluate congestion
        # Congestion strictly requires verified queue (>= 25m) and degraded speed (<= 65 km/h),
        # OR extreme occupancy (>= 75%)
        is_congested = (
            (max_queue >= 25.0 and avg_speed > 0 and avg_speed <= 65.0)
            or (avg_occ >= 75.0 and avg_speed > 0 and avg_speed <= 65.0)
        )
        if is_congested:
            return "CONGESTED", 0.95, ["STATE_CONGESTED"], total_lanes, avg_speed, avg_occ

        return "OPEN", 1.0, ["STATE_OPEN"], total_lanes, avg_speed, avg_occ

    def get_blocked_segments(self, estimates: List[Dict[str, Any]]) -> Set[str]:
        """Return the set of segment IDs currently assessed as CLOSED."""
        return {item["segment_id"] for item in estimates if item["state"] == "CLOSED"}

    def get_segment_penalties(self, estimates: List[Dict[str, Any]]) -> Dict[str, float]:
        """
        Return cost multiplier penalties for pathfinding:
        OPEN: 1.0
        CONGESTED: 2.2
        PARTIAL_BLOCK: 4.0
        UNKNOWN: 1.5
        """
        penalties = {}
        for item in estimates:
            st = item["state"]
            if st == "CONGESTED":
                penalties[item["segment_id"]] = 2.2
            elif st == "PARTIAL_BLOCK":
                penalties[item["segment_id"]] = 4.0
            elif st == "UNKNOWN":
                penalties[item["segment_id"]] = 1.5
            else:
                penalties[item["segment_id"]] = 1.0
        return penalties
