"""
Resource manager for corridor dispatching:
- Remote Support Pool: strictly <= 6 concurrent operator sessions with priority arbitration.
- Safe Stop Manager: real-time tracking and capacity/weight limit enforcement for 10 safe stops.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph


class RemoteSupportPool:
    """
    Manages operator pool for remote assistance.
    CRITICAL CONSTRAINT: In any step, concurrent sessions must be <= 6.
    """

    MAX_SESSIONS = 6

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        pool_cfg = self.ref.remote_support_pool
        self.max_sessions = int(pool_cfg.get("max_parallel_sessions", self.MAX_SESSIONS))
        self.session_setup_sec = int(pool_cfg.get("session_setup_sec", 20))

        # Active sessions: vehicle_id -> dict of session metadata
        self.active_sessions: Dict[str, Dict[str, Any]] = {}

    def allocate_sessions(
        self,
        step_index: int,
        candidates: List[Tuple[float, str, str]],  # (priority_score, vehicle_id, reason)
    ) -> Set[str]:
        """
        Arbitrate remote support requests for the current step.
        Candidates already having an active session retain it if they still request it.
        New candidates are allocated remaining slots based on priority_score (descending).
        Returns the set of vehicle IDs granted remote support in this step (strictly <= max_sessions).
        """
        requested_vids = {vid for _, vid, _ in candidates}
        candidate_map = {vid: (score, reason) for score, vid, reason in candidates}

        # 1. Retain sessions for vehicles that still need remote support
        retained_sessions: Dict[str, Dict[str, Any]] = {}
        for vid, sess in self.active_sessions.items():
            if vid in requested_vids:
                score, reason = candidate_map[vid]
                retained_sessions[vid] = {
                    "vehicle_id": vid,
                    "started_step": sess.get("started_step", step_index),
                    "last_active_step": step_index,
                    "reason": reason,
                    "priority_score": score,
                }

        # 2. Identify new candidate requests
        new_candidates = [
            (score, vid, reason)
            for score, vid, reason in candidates
            if vid not in retained_sessions
        ]
        # Sort new candidates by priority score descending
        new_candidates.sort(key=lambda x: x[0], reverse=True)

        # 3. Fill available slots
        available_slots = max(0, self.max_sessions - len(retained_sessions))
        new_admissions: Dict[str, Dict[str, Any]] = {}
        for score, vid, reason in new_candidates[:available_slots]:
            new_admissions[vid] = {
                "vehicle_id": vid,
                "started_step": step_index,
                "last_active_step": step_index,
                "reason": reason,
                "priority_score": score,
            }

        # 4. Update active sessions (strictly capped at max_sessions)
        self.active_sessions = {**retained_sessions, **new_admissions}

        # SAFETY INVARIANT: never exceed max_sessions
        if len(self.active_sessions) > self.max_sessions:
            sorted_sessions = sorted(
                self.active_sessions.items(),
                key=lambda item: item[1].get("priority_score", 0.0),
                reverse=True,
            )
            self.active_sessions = dict(sorted_sessions[:self.max_sessions])

        return set(self.active_sessions.keys())

    def release_session(self, vehicle_id: str) -> None:
        """Explicitly release a remote support session."""
        self.active_sessions.pop(vehicle_id, None)

    def is_active(self, vehicle_id: str) -> bool:
        """Check if vehicle has an active remote support session."""
        return vehicle_id in self.active_sessions

    def active_count(self) -> int:
        """Return current count of leased sessions."""
        return len(self.active_sessions)


class SafeStopManager:
    """
    Manages 10 safe stops (SS-01..SS-10).
    Enforces capacity limits and vehicle weight compatibility.
    """

    def __init__(self, graph: Optional[NetworkGraph] = None, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)
        self.safe_stops = self.ref.safe_stops

        # Occupancy tracking: safe_stop_id -> Set of vehicle_ids
        self.occupied_slots: Dict[str, Set[str]] = {
            sid: set() for sid in self.safe_stops
        }
        # Vehicle to safe stop mapping: vehicle_id -> safe_stop_id
        self.vehicle_allocations: Dict[str, str] = {}

    def get_occupancy(self, safe_stop_id: str) -> int:
        """Return current count of vehicles assigned to or occupying this safe stop."""
        return len(self.occupied_slots.get(safe_stop_id, set()))

    def get_occupancy_map(self) -> Dict[str, int]:
        """Return dict of {safe_stop_id: occupancy_count}."""
        return {sid: len(vids) for sid, vids in self.occupied_slots.items()}

    def get_capacity(self, safe_stop_id: str) -> int:
        """Return maximum vehicle capacity of safe stop."""
        info = self.safe_stops.get(safe_stop_id, {})
        return int(info.get("capacity_vehicles", 0))

    def get_max_weight_t(self, safe_stop_id: str) -> float:
        """Return maximum permissible vehicle mass for safe stop."""
        info = self.safe_stops.get(safe_stop_id, {})
        return float(info.get("max_vehicle_mass_t", 44.0))

    def is_compatible(
        self,
        safe_stop_id: str,
        vehicle_mass_t: float,
        blocked_segments: Set[str],
    ) -> bool:
        """
        Verify if a safe stop can legally and physically accept this vehicle:
        1. safe_stop_id exists
        2. safe stop has available capacity
        3. vehicle mass does not exceed safe stop limit
        4. safe stop segment is not CLOSED
        5. vehicle mass does not exceed segment weight limit
        """
        info = self.safe_stops.get(safe_stop_id)
        if not info:
            return False

        seg_id = info.get("segment_id")
        if not seg_id or seg_id in blocked_segments:
            return False

        seg = self.graph.segments.get(seg_id)
        if seg and vehicle_mass_t > seg.get("weight_limit_t", 44.0):
            return False

        if vehicle_mass_t > float(info.get("max_vehicle_mass_t", 44.0)):
            return False

        cap = int(info.get("capacity_vehicles", 0))
        cur_occ = len(self.occupied_slots.get(safe_stop_id, set()))
        if cur_occ >= cap:
            return False

        return True

    def assign_safe_stop(
        self,
        vehicle_id: str,
        vehicle_mass_t: float,
        current_segment_id: str,
        blocked_segments: Set[str],
    ) -> Optional[str]:
        """
        Find and reserve the nearest accessible safe stop.
        Returns safe_stop_id if reserved, or None if no valid safe stop available.
        """
        # If vehicle already has an active valid allocation, keep it
        existing_stop = self.vehicle_allocations.get(vehicle_id)
        if existing_stop and self.is_compatible(existing_stop, vehicle_mass_t, blocked_segments):
            return existing_stop

        # Search for reachable safe stop respecting current occupancies
        occ_map = self.get_occupancy_map()
        stop_tuple = self.graph.find_nearest_safe_stop(
            current_segment_id=current_segment_id,
            vehicle_mass_t=vehicle_mass_t,
            safe_stop_occupancy=occ_map,
            blocked_segments=blocked_segments,
        )

        if stop_tuple:
            stop_id, _, _ = stop_tuple
            if self.is_compatible(stop_id, vehicle_mass_t, blocked_segments):
                # Release previous stop if different
                if existing_stop and existing_stop != stop_id:
                    self.occupied_slots[existing_stop].discard(vehicle_id)

                self.occupied_slots[stop_id].add(vehicle_id)
                self.vehicle_allocations[vehicle_id] = stop_id
                return stop_id

        return None

    def release_safe_stop(self, vehicle_id: str) -> None:
        """Release reservation when vehicle leaves safe stop or resumes transit."""
        stop_id = self.vehicle_allocations.pop(vehicle_id, None)
        if stop_id and stop_id in self.occupied_slots:
            self.occupied_slots[stop_id].discard(vehicle_id)

    def sync_occupancies(
        self,
        vehicles_state: Dict[str, Dict[str, Any]],
        vehicle_actions: Dict[str, Dict[str, Any]],
    ) -> None:
        """
        Synchronize safe stop reservations with vehicle states and actions.
        Releases spots for vehicles that resumed moving without SAFE_STOP action.
        """
        for vid, stop_id in list(self.vehicle_allocations.items()):
            act = vehicle_actions.get(vid, {})
            v_data = vehicles_state.get(vid, {})
            action_name = act.get("motion_action")
            is_active = v_data.get("is_active", True)

            # If vehicle is inactive, or its action is no longer SAFE_STOP / HOLD, release
            if not is_active or (action_name not in ("SAFE_STOP", "HOLD")):
                self.release_safe_stop(vid)


class ResourceManager:
    """
    Central resource coordinator managing both Remote Support and Safe Stops.
    """

    def __init__(self, graph: Optional[NetworkGraph] = None, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)
        self.remote_pool = RemoteSupportPool(self.ref)
        self.safe_stops = SafeStopManager(self.graph, self.ref)
