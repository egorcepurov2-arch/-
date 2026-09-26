"""
Dynamic router and action stability module for autonomous vehicles.
Handles route validation, detour computation (Dijkstra), safe stop fallbacks,
hysteresis debounce (anti-oscillation), and remote support pool enforcement.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph


class CorridorRouter:
    """
    Manages vehicle routing, safety actions, action stability, and resource constraints.
    """

    MAX_REMOTE_SESSIONS = 6
    DEBOUNCE_STEPS = 6  # 30 seconds hysteresis window for congestion switches

    def __init__(self, graph: Optional[NetworkGraph] = None, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)

        self.vehicles = self.ref.vehicles
        self.vehicle_ids = self.ref.vehicle_ids

        # Route tracking per vehicle: vehicle_id -> list of segment IDs (downstream path)
        self.active_routes: Dict[str, List[str]] = {}

        # Last reroute step index for hysteresis
        self.last_reroute_step: Dict[str, int] = {vid: -999 for vid in self.vehicle_ids}

        # Safe stop occupancy tracker: safe_stop_id -> count of assigned vehicles
        self.safe_stop_occupancy: Dict[str, int] = {
            stop_id: 0 for stop_id in self.ref.safe_stops
        }

    def plan_vehicle_actions(
        self,
        step_index: int,
        segment_estimates: List[Dict[str, Any]],
        vehicles_state: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Generate decisions and actions for all 72 vehicles for the current step.
        """
        # Reset safe stop temporary occupancy for re-accounting
        self.safe_stop_occupancy = {stop_id: 0 for stop_id in self.ref.safe_stops}

        blocked_segments = {
            item["segment_id"] for item in segment_estimates if item["state"] == "CLOSED"
        }
        penalties: Dict[str, float] = {}
        for item in segment_estimates:
            st = item["state"]
            if st == "CONGESTED":
                penalties[item["segment_id"]] = 2.2
            elif st == "PARTIAL_BLOCK":
                penalties[item["segment_id"]] = 4.0
            elif st == "UNKNOWN":
                penalties[item["segment_id"]] = 1.5
            else:
                penalties[item["segment_id"]] = 1.0

        provisional_actions: Dict[str, Dict[str, Any]] = {}
        remote_candidates: List[Tuple[int, str]] = []  # (priority_weight, vehicle_id)

        for vid in self.vehicle_ids:
            v_data = vehicles_state.get(vid, {})
            is_active = v_data.get("is_active", True)
            cur_seg = v_data.get("current_segment")

            # Fallback static metadata
            v_catalog = self.vehicles.get(vid, {})
            mass = v_catalog.get("gross_mass_t", 25.0)
            target_hub = v_data.get("destination_hub_id") or v_catalog.get("destination_hub_id", "HUB-02")
            profile_id = v_data.get("odd_profile_id") or v_catalog.get("odd_profile_id", "ODD-A")
            cargo_priority = v_data.get("cargo_priority") or v_catalog.get("cargo_priority", 3)

            # Profile structural restrictions (e.g. ODD-C forbids tunnels)
            forbidden_structures: Set[str] = set()
            if profile_id == "ODD-C":
                forbidden_structures.add("tunnel")

            # 1. Inactive vehicle handling
            if not is_active or not cur_seg:
                provisional_actions[vid] = {
                    "vehicle_id": vid,
                    "motion_action": "NO_ACTION",
                    "remote_support_required": False,
                    "confidence": 1.0,
                    "rationale_codes": ["AT_HUB_OR_INACTIVE"],
                }
                continue

            # 2. Check if vehicle is already on a CLOSED segment
            if cur_seg in blocked_segments:
                stop_tuple = self.graph.find_nearest_safe_stop(
                    current_segment_id=cur_seg,
                    vehicle_mass_t=mass,
                    safe_stop_occupancy=self.safe_stop_occupancy,
                    blocked_segments=blocked_segments,
                )
                if stop_tuple:
                    stop_id, _, _ = stop_tuple
                    self.safe_stop_occupancy[stop_id] = self.safe_stop_occupancy.get(stop_id, 0) + 1
                    provisional_actions[vid] = {
                        "vehicle_id": vid,
                        "motion_action": "SAFE_STOP",
                        "safe_stop_id": stop_id,
                        "remote_support_required": False,
                        "confidence": 0.98,
                        "rationale_codes": ["ROUTE_CLOSED"],
                    }
                else:
                    provisional_actions[vid] = {
                        "vehicle_id": vid,
                        "motion_action": "HOLD",
                        "remote_support_required": True,
                        "confidence": 0.95,
                        "rationale_codes": ["ROUTE_CLOSED"],
                    }
                    priority_weight = 6 - cargo_priority
                    remote_candidates.append((priority_weight, vid))
                continue

            # 3. Active vehicle route evaluation
            assigned_route = self.active_routes.get(vid)

            # Check remaining path downstream of current segment
            remaining_route: Optional[List[str]] = None
            if assigned_route:
                if cur_seg in assigned_route:
                    idx = assigned_route.index(cur_seg)
                    remaining_route = assigned_route[idx + 1:]
                else:
                    remaining_route = assigned_route

            # Verify if existing route is still physically blocked
            route_is_blocked = False
            if remaining_route:
                route_is_blocked = self.graph.is_route_blocked(remaining_route, blocked_segments)
            else:
                route_is_blocked = True

            if route_is_blocked or not remaining_route:
                # Find new shortest path bypassing blocked segments
                detour = self.graph.find_shortest_path(
                    start_segment_id=cur_seg,
                    target_hub_id=target_hub,
                    vehicle_mass_t=mass,
                    blocked_segments=blocked_segments,
                    forbidden_structures=forbidden_structures,
                    segment_penalties=penalties,
                    after_current=True,
                )

                if detour is not None and len(detour) > 0:
                    self.active_routes[vid] = detour
                    self.last_reroute_step[vid] = step_index

                    if remaining_route is not None and route_is_blocked:
                        # Legitimate reroute due to closure
                        provisional_actions[vid] = {
                            "vehicle_id": vid,
                            "motion_action": "REROUTE",
                            "route_segment_ids": detour,
                            "remote_support_required": False,
                            "confidence": 0.98,
                            "rationale_codes": ["ROUTE_CLOSED"],
                        }
                    else:
                        # Baseline route initialization / continued valid movement
                        provisional_actions[vid] = {
                            "vehicle_id": vid,
                            "motion_action": "CONTINUE",
                            "remote_support_required": False,
                            "confidence": 1.0,
                            "rationale_codes": ["STATE_OPEN"],
                        }
                elif detour is not None and len(detour) == 0:
                    # Vehicle already arrived at terminal segment entering target hub
                    provisional_actions[vid] = {
                        "vehicle_id": vid,
                        "motion_action": "CONTINUE",
                        "remote_support_required": False,
                        "confidence": 1.0,
                        "rationale_codes": ["STATE_OPEN"],
                    }
                else:
                    # No viable route to destination hub -> safe stop fallback
                    stop_tuple = self.graph.find_nearest_safe_stop(
                        current_segment_id=cur_seg,
                        vehicle_mass_t=mass,
                        safe_stop_occupancy=self.safe_stop_occupancy,
                        blocked_segments=blocked_segments,
                    )
                    if stop_tuple:
                        stop_id, _, _ = stop_tuple
                        self.safe_stop_occupancy[stop_id] = self.safe_stop_occupancy.get(stop_id, 0) + 1
                        provisional_actions[vid] = {
                            "vehicle_id": vid,
                            "motion_action": "SAFE_STOP",
                            "safe_stop_id": stop_id,
                            "remote_support_required": False,
                            "confidence": 0.98,
                            "rationale_codes": ["ROUTE_CLOSED"],
                        }
                    else:
                        provisional_actions[vid] = {
                            "vehicle_id": vid,
                            "motion_action": "HOLD",
                            "remote_support_required": True,
                            "confidence": 0.95,
                            "rationale_codes": ["ROUTE_CLOSED"],
                        }
                        priority_weight = 6 - cargo_priority
                        remote_candidates.append((priority_weight, vid))
            else:
                # Existing route is NOT blocked
                # Action Stability / Hysteresis check (TASK-08)
                steps_since_reroute = step_index - self.last_reroute_step[vid]
                cur_cost = sum(
                    self.graph.segments[s]["length_m"] * penalties.get(s, 1.0)
                    for s in remaining_route if s in self.graph.segments
                )
                base_len = sum(
                    self.graph.segments[s]["length_m"]
                    for s in remaining_route if s in self.graph.segments
                )

                has_congestion = cur_cost > base_len * 1.5

                if has_congestion and steps_since_reroute >= self.DEBOUNCE_STEPS:
                    alt_detour = self.graph.find_shortest_path(
                        start_segment_id=cur_seg,
                        target_hub_id=target_hub,
                        vehicle_mass_t=mass,
                        blocked_segments=blocked_segments,
                        forbidden_structures=forbidden_structures,
                        segment_penalties=penalties,
                        after_current=True,
                    )
                    if alt_detour and len(alt_detour) > 0:
                        alt_cost = sum(
                            self.graph.segments[s]["length_m"] * penalties.get(s, 1.0)
                            for s in alt_detour if s in self.graph.segments
                        )
                        if alt_cost < cur_cost * 0.75:  # At least 25% cost saving
                            self.active_routes[vid] = alt_detour
                            self.last_reroute_step[vid] = step_index
                            provisional_actions[vid] = {
                                "vehicle_id": vid,
                                "motion_action": "REROUTE",
                                "route_segment_ids": alt_detour,
                                "remote_support_required": False,
                                "confidence": 0.95,
                                "rationale_codes": ["ROUTE_CONGESTED"],
                            }
                            continue

                # No reroute required: maintain stable motion
                provisional_actions[vid] = {
                    "vehicle_id": vid,
                    "motion_action": "CONTINUE",
                    "remote_support_required": False,
                    "confidence": 1.0,
                    "rationale_codes": ["STATE_OPEN"],
                }

        # 4. Strict Remote Support Pool Arbitration (Max 6 sessions)
        remote_candidates.sort(key=lambda x: x[0], reverse=True)
        allowed_vids = {vid for _, vid in remote_candidates[:self.MAX_REMOTE_SESSIONS]}

        final_actions: List[Dict[str, Any]] = []
        for vid in self.vehicle_ids:
            act = provisional_actions[vid]
            if act["remote_support_required"]:
                if vid in allowed_vids:
                    act["remote_support_required"] = True
                else:
                    # Pool limit reached: revoke remote session to protect Safety Gate
                    act["remote_support_required"] = False
                    if "REMOTE_SUPPORT_CAPACITY" not in act["rationale_codes"]:
                        act["rationale_codes"].append("REMOTE_SUPPORT_CAPACITY")
            final_actions.append(act)

        return final_actions
