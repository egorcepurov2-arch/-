"""
Safety Guard policy and logistics priority manager.
Enforces safety constraints, mitigates ODD violations, allocates the remote
support pool (<= 6 concurrent sessions) by cargo priority, and commands safe stops.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph


class SafetyGuard:
    """
    Arbitrates actions between routing intentions, ODD compliance, and logistics priorities.
    Ensures zero critical safety gate violations.
    """

    MAX_REMOTE_SESSIONS = 6

    def __init__(self, graph: Optional[NetworkGraph] = None, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)

        self.vehicles = self.ref.vehicles
        self.vehicle_ids = self.ref.vehicle_ids
        self.transport_orders = self.ref.transport_orders
        self.safe_stops = self.ref.safe_stops

        # Vehicle order mapping: vehicle_id -> order dict
        self.vehicle_to_order: Dict[str, Dict[str, Any]] = {}
        for ord_id, o in self.transport_orders.items():
            vid = o.get("vehicle_id")
            if vid:
                self.vehicle_to_order[vid] = o

    def arbitrate_actions(
        self,
        step_index: int,
        provisional_actions: List[Dict[str, Any]],
        odd_assessments: List[Dict[str, Any]],
        vehicles_state: Dict[str, Dict[str, Any]],
        blocked_segments: Set[str],
    ) -> List[Dict[str, Any]]:
        """
        Merge router actions with ODD assessments, apply cargo priorities,
        and strictly cap remote support pool at <= 6 sessions.
        """
        # Index provisional actions and ODD by vehicle_id
        action_by_vid = {a["vehicle_id"]: dict(a) for a in provisional_actions}
        odd_by_vid = {o["vehicle_id"]: o for o in odd_assessments}

        # Safe stop occupancy counter for this step
        safe_stop_occupancy: Dict[str, int] = {sid: 0 for sid in self.safe_stops}

        # 1. Identify all vehicles demanding remote support
        # Candidates scored by cargo priority and penalty per min
        remote_candidates: List[Tuple[float, str]] = []

        for vid in self.vehicle_ids:
            act = action_by_vid.get(vid, {})
            odd = odd_by_vid.get(vid, {})
            v_data = vehicles_state.get(vid, {})
            is_active = v_data.get("is_active", True)
            cur_seg = v_data.get("current_segment")

            if not is_active or not cur_seg:
                continue

            order = self.vehicle_to_order.get(vid, {})
            cargo_pri = v_data.get("cargo_priority") or order.get("priority", 3)
            penalty_min = order.get("penalty_per_min", 1.0)
            priority_score = (6.0 - cargo_pri) * 20.0 + float(penalty_min)

            odd_violated = (odd.get("odd_status") == "VIOLATED")
            router_wants_remote = act.get("remote_support_required", False)

            if odd_violated or router_wants_remote:
                remote_candidates.append((priority_score, vid))

        # Sort remote candidates by priority score descending
        remote_candidates.sort(key=lambda x: x[0], reverse=True)
        admitted_remote_vids = {vid for _, vid in remote_candidates[:self.MAX_REMOTE_SESSIONS]}

        # 2. Finalize actions for each vehicle
        final_actions: List[Dict[str, Any]] = []

        for vid in self.vehicle_ids:
            act = action_by_vid.get(vid, {})
            odd = odd_by_vid.get(vid, {})
            v_data = vehicles_state.get(vid, {})
            is_active = v_data.get("is_active", True)
            cur_seg = v_data.get("current_segment")

            # Handle inactive vehicles
            if not is_active or not cur_seg:
                final_actions.append({
                    "vehicle_id": vid,
                    "motion_action": "NO_ACTION",
                    "remote_support_required": False,
                    "confidence": 1.0,
                    "rationale_codes": ["AT_HUB_OR_INACTIVE"],
                })
                continue

            odd_status = odd.get("odd_status", "COMPLIANT")
            violations = odd.get("violation_codes", [])
            v_catalog = self.vehicles.get(vid, {})
            mass = v_data.get("gross_mass_t") or v_catalog.get("gross_mass_t", 25.0)

            # Case A: ODD is COMPLIANT
            if odd_status != "VIOLATED":
                # Router action is safe to execute directly
                # Ensure remote_support_required respects the pool
                if act.get("remote_support_required"):
                    act["remote_support_required"] = (vid in admitted_remote_vids)
                final_actions.append(act)
                continue

            # Case B: ODD is VIOLATED (Safety Limiter Trigger)
            # CRITICAL: Active vehicle outside ODD MUST NOT have CONTINUE without remote support!
            odd_rationales = [f"ODD_{v}" for v in violations]

            if vid in admitted_remote_vids:
                # Vehicle successfully acquired remote support supervisor
                # It can proceed with LIMIT_SPEED or CONTINUE safely under remote oversight
                if act.get("motion_action") == "REROUTE":
                    # Keep reroute under remote guidance
                    act["remote_support_required"] = True
                    for r in odd_rationales:
                        if r not in act["rationale_codes"]:
                            act["rationale_codes"].append(r)
                    final_actions.append(act)
                else:
                    # Limit speed under remote supervisor
                    final_actions.append({
                        "vehicle_id": vid,
                        "motion_action": "LIMIT_SPEED",
                        "speed_limit_kmh": 40.0,
                        "remote_support_required": True,
                        "confidence": 0.98,
                        "rationale_codes": odd_rationales,
                    })
            else:
                # Remote support pool is full!
                # Vehicle cannot continue movement outside ODD autonomously -> Divert to SAFE_STOP
                stop_tuple = self.graph.find_nearest_safe_stop(
                    current_segment_id=cur_seg,
                    vehicle_mass_t=mass,
                    safe_stop_occupancy=safe_stop_occupancy,
                    blocked_segments=blocked_segments,
                )

                if stop_tuple:
                    stop_id, _, _ = stop_tuple
                    safe_stop_occupancy[stop_id] = safe_stop_occupancy.get(stop_id, 0) + 1
                    final_actions.append({
                        "vehicle_id": vid,
                        "motion_action": "SAFE_STOP",
                        "safe_stop_id": stop_id,
                        "remote_support_required": False,
                        "confidence": 0.98,
                        "rationale_codes": ["SAFE_STOP_CAPACITY", "REMOTE_SUPPORT_CAPACITY"] + odd_rationales,
                    })
                else:
                    # No reachable safe stop -> Emergency HOLD in current lane/shoulder
                    final_actions.append({
                        "vehicle_id": vid,
                        "motion_action": "HOLD",
                        "remote_support_required": False,
                        "confidence": 0.95,
                        "rationale_codes": ["REMOTE_SUPPORT_CAPACITY"] + odd_rationales,
                    })

        return final_actions
