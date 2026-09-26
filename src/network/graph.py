"""
Network topology graph for the autonomous vehicle corridor.
Maintains nodes, 86 directed segments, successors, and constraint-aware pathfinding.
"""

import heapq
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import ReferenceData, load_reference_data


class NetworkGraph:
    """
    Directed graph representing the 86 corridor segments and 34 nodes.
    Supports routing with weight limits, structure restrictions, and dynamic closures.
    """

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()

        self.nodes = self.ref.nodes
        self.segments = self.ref.segments
        self.segment_ids = self.ref.segment_ids

        # Adjacency mappings
        self.node_outgoing_segments: Dict[str, List[str]] = defaultdict(list)
        self.node_incoming_segments: Dict[str, List[str]] = defaultdict(list)

        for sid, seg in self.segments.items():
            self.node_outgoing_segments[seg["from_node"]].append(sid)
            self.node_incoming_segments[seg["to_node"]].append(sid)

        # Line-graph successors: segment_id -> list of next connected segments
        self.segment_successors: Dict[str, List[str]] = {}
        for sid, seg in self.segments.items():
            to_node = seg["to_node"]
            self.segment_successors[sid] = list(self.node_outgoing_segments.get(to_node, []))

        # Hub node mapping: hub_id -> node_id
        self.hub_nodes: Dict[str, str] = {
            hid: hub["node_id"] for hid, hub in self.ref.hubs.items()
        }

        # Safe stops mapping: segment_id -> list of safe_stop_ids on this segment
        self.segment_to_safe_stops: Dict[str, List[str]] = defaultdict(list)
        for stop_id, stop in self.ref.safe_stops.items():
            self.segment_to_safe_stops[stop["segment_id"]].append(stop_id)

    def get_successors(self, segment_id: str) -> List[str]:
        """Return downstream connected segments."""
        return self.segment_successors.get(segment_id, [])

    def is_valid_route(
        self,
        route_segment_ids: List[str],
        max_weight_t: float = 44.0,
        forbidden_structures: Optional[Set[str]] = None,
    ) -> Tuple[bool, str]:
        """
        Validate that a proposed route is continuous, respects direction,
        weight limits and forbidden structures (e.g. tunnels).
        """
        if not route_segment_ids:
            return False, "Route is empty"

        if forbidden_structures is None:
            forbidden_structures = set()

        for i, sid in enumerate(route_segment_ids):
            if sid not in self.segments:
                return False, f"Unknown segment ID: {sid}"

            seg = self.segments[sid]

            # Check AV allowed
            if not seg.get("av_allowed", True):
                return False, f"Segment {sid} forbids AV traffic"

            # Check weight limit
            if max_weight_t > seg.get("weight_limit_t", 44.0):
                return False, f"Segment {sid} weight limit {seg.get('weight_limit_t')}t < {max_weight_t}t"

            # Check structure restrictions
            if seg.get("structure") in forbidden_structures:
                return False, f"Segment {sid} structure '{seg.get('structure')}' is forbidden"

            # Check continuity with next segment
            if i < len(route_segment_ids) - 1:
                next_sid = route_segment_ids[i + 1]
                if next_sid not in self.segment_successors.get(sid, []):
                    return False, f"Disconnected route: {sid} does not lead to {next_sid}"

        return True, "Valid"

    def find_shortest_path(
        self,
        start_segment_id: str,
        target_hub_id: str,
        vehicle_mass_t: float = 20.0,
        blocked_segments: Optional[Set[str]] = None,
        forbidden_structures: Optional[Set[str]] = None,
        segment_penalties: Optional[Dict[str, float]] = None,
        after_current: bool = True,
    ) -> Optional[List[str]]:
        """
        Find shortest cost path from start_segment_id to target_hub_id using Dijkstra.
        Respects one-way direction, vehicle mass, blocked segments, and structure restrictions.
        If after_current is True (default for REROUTE action), returns the path strictly AFTER
        the current segment starting at current_segment.to_node and ending at destination HUB node.
        """
        if start_segment_id not in self.segments:
            return None

        target_node = self.hub_nodes.get(target_hub_id)
        if not target_node:
            return None

        start_seg = self.segments[start_segment_id]
        if after_current and start_seg["to_node"] == target_node:
            # Already on the segment entering target hub node
            return []

        if blocked_segments is None:
            blocked_segments = set()
        if forbidden_structures is None:
            forbidden_structures = set()
        if segment_penalties is None:
            segment_penalties = {}

        # Priority queue entries: (cumulative_cost, current_segment_id, path_list)
        pq: List[Tuple[float, str, List[str]]] = []
        visited: Set[str] = set()

        if after_current:
            # Dijkstra starts from successor segments of start_segment_id
            for nxt_id in self.segment_successors.get(start_segment_id, []):
                if nxt_id in blocked_segments:
                    continue
                nxt_seg = self.segments[nxt_id]
                if vehicle_mass_t > nxt_seg.get("weight_limit_t", 44.0):
                    continue
                if nxt_seg.get("structure") in forbidden_structures:
                    continue
                if not nxt_seg.get("av_allowed", True):
                    continue
                cost = nxt_seg["length_m"] * segment_penalties.get(nxt_id, 1.0)
                heapq.heappush(pq, (cost, nxt_id, [nxt_id]))
        else:
            pq.append((0.0, start_segment_id, [start_segment_id]))

        while pq:
            cost, cur_id, path = heapq.heappop(pq)

            cur_seg = self.segments[cur_id]
            # Check if current segment reaches target node
            if cur_seg["to_node"] == target_node:
                return path

            if cur_id in visited:
                continue
            visited.add(cur_id)

            for nxt_id in self.segment_successors.get(cur_id, []):
                if nxt_id in visited or nxt_id in blocked_segments:
                    continue

                nxt_seg = self.segments[nxt_id]

                # Weight limit check
                if vehicle_mass_t > nxt_seg.get("weight_limit_t", 44.0):
                    continue

                # Structure check
                if nxt_seg.get("structure") in forbidden_structures:
                    continue

                # AV allowed check
                if not nxt_seg.get("av_allowed", True):
                    continue

                multiplier = segment_penalties.get(nxt_id, 1.0)
                nxt_cost = cost + nxt_seg["length_m"] * multiplier
                heapq.heappush(pq, (nxt_cost, nxt_id, path + [nxt_id]))

        return None

    def get_route_length_m(self, route_segment_ids: List[str]) -> float:
        """Return total geometric distance of a route."""
        return sum(self.segments[s]["length_m"] for s in route_segment_ids if s in self.segments)

    def is_route_blocked(
        self,
        route_segment_ids: List[str],
        blocked_segments: Set[str],
    ) -> bool:
        """Check if any segment on the route is in blocked_segments."""
        return any(sid in blocked_segments for sid in route_segment_ids)

    def find_nearest_safe_stop(
        self,
        current_segment_id: str,
        vehicle_mass_t: float,
        safe_stop_occupancy: Dict[str, int],
        blocked_segments: Optional[Set[str]] = None,
        max_search_depth: int = 6,
    ) -> Optional[Tuple[str, str, List[str]]]:
        """
        Search downstream along reachable segments to find the nearest valid safe stop.
        Checks:
        1. safe stop capacity > occupied
        2. safe stop max_vehicle_mass_t >= vehicle_mass_t
        3. reachable without passing through blocked segments

        Returns: (safe_stop_id, target_segment_id, route_to_stop) or None
        """
        if current_segment_id not in self.segments:
            return None

        if blocked_segments is None:
            blocked_segments = set()

        # BFS on segment graph to find closest reachable safe stop
        queue: List[Tuple[str, List[str]]] = [(current_segment_id, [current_segment_id])]
        visited: Set[str] = {current_segment_id}

        while queue:
            cur_id, path = queue.pop(0)

            # Check if current segment has safe stops
            stops_on_seg = self.segment_to_safe_stops.get(cur_id, [])
            for stop_id in stops_on_seg:
                stop_info = self.ref.safe_stops.get(stop_id, {})
                cap = stop_info.get("capacity_vehicles", 2)
                occupied = safe_stop_occupancy.get(stop_id, 0)
                max_mass = stop_info.get("max_vehicle_mass_t", 44.0)

                if occupied < cap and vehicle_mass_t <= max_mass:
                    return stop_id, cur_id, path

            if len(path) > max_search_depth:
                continue

            for nxt_id in self.segment_successors.get(cur_id, []):
                if nxt_id not in visited and nxt_id not in blocked_segments:
                    visited.add(nxt_id)
                    queue.append((nxt_id, path + [nxt_id]))

        return None
