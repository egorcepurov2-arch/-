"""
Safety Guard policy and logistics priority manager.
Enforces safety constraints, mitigates ODD violations, allocates the remote
support pool (<= 6 concurrent sessions) by cargo priority, and commands safe stops.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph
from src.dispatch.resource_manager import ResourceManager
from src.dispatch.hub_manager import HubManager


class SafetyGuard:
    """
    Arbitrates actions between routing intentions, ODD compliance, and logistics priorities.
    Ensures zero critical safety gate violations.
    """

    MAX_REMOTE_SESSIONS = 6

    def __init__(
        self,
        graph: Optional[NetworkGraph] = None,
        ref: Optional[ReferenceData] = None,
        resource_manager: Optional[ResourceManager] = None,
        hub_manager: Optional[HubManager] = None,
    ):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)
        self.resource_manager = (
            resource_manager if resource_manager is not None
            else ResourceManager(self.graph, self.ref)
        )
        self.hub_manager = (
            hub_manager if hub_manager is not None
            else HubManager(self.graph, self.ref)
        )

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
        resource_manager: Optional[ResourceManager] = None,
        hub_manager: Optional[HubManager] = None,
    ) -> List[Dict[str, Any]]:
        """
        Merge router actions with ODD assessments, apply cargo priorities,
        and strictly cap remote support pool at <= 6 sessions using ResourceManager.
        """
        res_mgr = resource_manager if resource_manager is not None else self.resource_manager
        hub_mgr = hub_manager if hub_manager is not None else self.hub_manager

        # Synchronize MAX_REMOTE_SESSIONS override if modified externally (e.g. in tests)
        res_mgr.remote_pool.max_sessions = self.MAX_REMOTE_SESSIONS

        action_by_vid = {a["vehicle_id"]: dict(a) for a in provisional_actions}
        odd_by_vid = {o["vehicle_id"]: o for o in odd_assessments}

        # 1. Identify all vehicles demanding remote support
        remote_candidates: List[Tuple[float, str, str]] = []

        for vid in self.vehicle_ids:
            act = action_by_vid.get(vid, {})
            odd = odd_by_vid.get(vid, {})
            v_data = vehicles_state.get(vid, {})
            is_active = v_data.get("is_active", True)
            cur_seg = v_data.get("current_segment")

            if not is_active or not cur_seg:
                res_mgr.remote_pool.release_session(vid)
                res_mgr.safe_stops.release_safe_stop(vid)
                continue

            order = self.vehicle_to_order.get(vid, {})
            cargo_pri = v_data.get("cargo_priority") or order.get("priority", 3)
            penalty_min = order.get("penalty_per_min", 1.0)
            priority_score = (6.0 - cargo_pri) * 20.0 + float(penalty_min)

            odd_violated = (odd.get("odd_status") == "VIOLATED")
            router_wants_remote = act.get("remote_support_required", False)

            if odd_violated:
                remote_candidates.append((priority_score, vid, "ODD_VIOLATION"))
            elif router_wants_remote:
                remote_candidates.append((priority_score, vid, "ROUTER_REQUEST"))

        # 2. Allocate remote support sessions strictly bounded by max_sessions
        admitted_remote_vids = res_mgr.remote_pool.allocate_sessions(
            step_index=step_index,
            candidates=remote_candidates,
        )

        # 3. Finalize actions for each vehicle
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

            dest_hub = v_data.get("destination_hub_id", "HUB-02")

            # Check Hub egress and destination saturation gating
            should_hold, hold_reason = hub_mgr.should_hold_at_hub(
                current_segment=cur_seg,
                destination_hub_id=dest_hub,
                blocked_segments=blocked_segments,
            )
            if should_hold:
                res_mgr.remote_pool.release_session(vid)
                res_mgr.safe_stops.release_safe_stop(vid)
                final_actions.append({
                    "vehicle_id": vid,
                    "motion_action": "HOLD",
                    "remote_support_required": False,
                    "confidence": 0.98,
                    "rationale_codes": [hold_reason],
                })
                continue

            odd_status = odd.get("odd_status", "COMPLIANT")
            violations = odd.get("violation_codes", [])
            v_catalog = self.vehicles.get(vid, {})
            mass = v_data.get("gross_mass_t") or v_catalog.get("gross_mass_t", 25.0)

            # Case A: ODD is COMPLIANT
            if odd_status != "VIOLATED":
                if act.get("remote_support_required"):
                    act["remote_support_required"] = (vid in admitted_remote_vids)
                else:
                    act["remote_support_required"] = False

                # If resuming transit, release safe stop
                if act.get("motion_action") in ("CONTINUE", "REROUTE"):
                    res_mgr.safe_stops.release_safe_stop(vid)

                final_actions.append(act)
                continue

            # Case B: ODD is VIOLATED (Safety Limiter Trigger)
            odd_rationales = [f"ODD_{v}" for v in violations]

            if vid in admitted_remote_vids:
                # Vehicle acquired remote support
                if act.get("motion_action") == "REROUTE":
                    act["remote_support_required"] = True
                    for r in odd_rationales:
                        if r not in act["rationale_codes"]:
                            act["rationale_codes"].append(r)
                    final_actions.append(act)
                else:
                    final_actions.append({
                        "vehicle_id": vid,
                        "motion_action": "LIMIT_SPEED",
                        "speed_limit_kmh": 40.0,
                        "remote_support_required": True,
                        "confidence": 0.98,
                        "rationale_codes": odd_rationales,
                    })
            else:
                # Remote support pool is exhausted -> Divert to SAFE_STOP
                stop_id = res_mgr.safe_stops.assign_safe_stop(
                    vehicle_id=vid,
                    vehicle_mass_t=mass,
                    current_segment_id=cur_seg,
                    blocked_segments=blocked_segments,
                )

                if stop_id:
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

        # 4. Sync safe stop occupancies with finalized vehicle actions
        action_map = {a["vehicle_id"]: a for a in final_actions}
        res_mgr.safe_stops.sync_occupancies(vehicles_state, action_map)

        return final_actions
