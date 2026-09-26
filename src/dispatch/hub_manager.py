"""
Logistics hub queue tracker and egress dispatcher.
Monitors terminal status (HUB-01..HUB-04), queue depths, service rates,
and prevents departures into blocked or oversaturated corridor segments.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data
from src.network.graph import NetworkGraph


class HubManager:
    """
    Manages logistics hubs (HUB-01..HUB-04) and coordinates vehicle departures.
    """

    def __init__(self, graph: Optional[NetworkGraph] = None, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()
        self.graph = graph if graph is not None else NetworkGraph(self.ref)

        self.hubs = self.ref.hubs

        # Hub state tracking
        self.hub_status: Dict[str, Dict[str, Any]] = {}
        for hid, h in self.hubs.items():
            self.hub_status[hid] = {
                "hub_id": hid,
                "node_id": h["node_id"],
                "capacity_vehicles": int(h.get("capacity_vehicles", 30)),
                "capacity_used_pct": 20.0,
                "queue_vehicles": 0.0,
                "service_rate_vph": float(h.get("service_rate_vph", 20.0)),
                "accepting_new_arrivals": True,
                "estimated_wait_min": 0.0,
                "last_update_step": 0,
            }

        # Hub node to hub_id mapping
        self.node_to_hub: Dict[str, str] = {
            h["node_id"]: hid for hid, h in self.hubs.items()
        }

    def update_from_events(self, step_index: int, events: List[Dict[str, Any]]) -> None:
        """Update terminal queues and capacity usage from HUB_STATUS events."""
        for ev in events:
            if ev.get("event_type") == "HUB_STATUS":
                hid = ev.get("hub_id")
                if hid and hid in self.hub_status:
                    st = self.hub_status[hid]
                    if ev.get("queue_vehicles") is not None:
                        st["queue_vehicles"] = float(ev["queue_vehicles"])
                    if ev.get("capacity_used_pct") is not None:
                        st["capacity_used_pct"] = float(ev["capacity_used_pct"])
                    if ev.get("accepting_new_arrivals") is not None:
                        st["accepting_new_arrivals"] = bool(ev["accepting_new_arrivals"])
                    if ev.get("estimated_wait_min") is not None:
                        st["estimated_wait_min"] = float(ev["estimated_wait_min"])
                    st["last_update_step"] = step_index

    def is_destination_accessible(self, destination_hub_id: str) -> bool:
        """Check if destination hub is accepting arrivals without critical gridlock."""
        st = self.hub_status.get(destination_hub_id)
        if not st:
            return True
        # If terminal explicitly rejects arrivals or is > 98% full
        if not st["accepting_new_arrivals"] or st["capacity_used_pct"] >= 98.0:
            return False
        return True

    def is_hub_egress_blocked(
        self,
        hub_id: str,
        blocked_segments: Set[str],
    ) -> bool:
        """Check if all egress segments leaving a hub are blocked."""
        st = self.hub_status.get(hub_id)
        if not st:
            return False
        node_id = st["node_id"]
        outgoing = self.graph.node_outgoing_segments.get(node_id, [])
        if not outgoing:
            return False
        # If all outgoing segments are in blocked_segments
        return all(sid in blocked_segments for sid in outgoing)

    def should_hold_at_hub(
        self,
        current_segment: str,
        destination_hub_id: str,
        blocked_segments: Set[str],
    ) -> Tuple[bool, str]:
        """
        Determine if a vehicle located at or entering a hub should HOLD.
        Returns: (should_hold, rationale_code)
        """
        # If destination hub is locked / full
        if not self.is_destination_accessible(destination_hub_id):
            return True, "ROUTE_HUB_CAPACITY"

        # Check if current segment starts at a hub node
        seg = self.graph.segments.get(current_segment)
        if seg:
            from_node = seg["from_node"]
            if from_node in self.node_to_hub:
                hub_id = self.node_to_hub[from_node]
                if self.is_hub_egress_blocked(hub_id, blocked_segments):
                    return True, "ROUTE_CLOSED"

        return False, ""
