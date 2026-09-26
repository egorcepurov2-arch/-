"""
Operational Design Domain (ODD) engine and spatial environment mapping module.
Maintains weather observation state, V2X availability, and validates vehicle
telemetry against ODD profiles (ODD-A, ODD-B, ODD-C, ODD-D).
"""

import math
from typing import Any, Dict, List, Optional, Set, Tuple
from src.config import ReferenceData, load_reference_data


class ODDEngine:
    """
    Evaluates ODD compliance for all 72 corridor vehicles.
    Maps physical segments to nearest weather stations and RSU coverage zones.
    """

    def __init__(self, ref: Optional[ReferenceData] = None):
        self.ref = ref if ref is not None else load_reference_data()

        self.odd_profiles = self.ref.odd_profiles
        self.vehicles = self.ref.vehicles
        self.segments = self.ref.segments
        self.nodes = self.ref.nodes
        self.weather_stations = self.ref.weather_stations
        self.rsus = self.ref.rsu_catalog

        # Precompute segment -> nearest weather station ID
        self.segment_to_weather: Dict[str, str] = self._build_weather_mapping()

        # Precompute segment -> RSU ID via v2x_zone
        self.segment_to_rsu: Dict[str, str] = self._build_rsu_mapping()

        # Dynamic state: weather conditions per station
        self.weather_by_station: Dict[str, Dict[str, Any]] = {}
        for st_id in self.weather_stations:
            self.weather_by_station[st_id] = {
                "visibility_m": 1000.0,
                "rain_level": 0,
                "precipitation_mm_h": 0.0,
                "wind_mps": 3.0,
                "road_surface": "DRY",
                "air_temperature_c": 18.0,
            }

        # Dynamic state: V2X availability per RSU ID
        self.rsu_available: Dict[str, bool] = {rsu_id: True for rsu_id in self.rsus}

    def _build_weather_mapping(self) -> Dict[str, str]:
        """Precompute the closest weather station for each segment center."""
        node_coords = {
            nid: (n["x_m"], n["y_m"]) for nid, n in self.nodes.items()
        }
        station_coords = {
            sid: (s["x_m"], s["y_m"]) for sid, s in self.weather_stations.items()
        }

        mapping = {}
        for sid, seg in self.segments.items():
            x1, y1 = node_coords[seg["from_node"]]
            x2, y2 = node_coords[seg["to_node"]]
            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0

            best_st = None
            min_dist = float("inf")
            for st_id, (sx, sy) in station_coords.items():
                d = math.hypot(mid_x - sx, mid_y - sy)
                if d < min_dist:
                    min_dist = d
                    best_st = st_id
            mapping[sid] = best_st or "WX-01"
        return mapping

    def _build_rsu_mapping(self) -> Dict[str, str]:
        """Map each segment to the RSU covering its v2x_zone."""
        zone_to_rsu = {
            rsu["v2x_zone"]: rsu_id for rsu_id, rsu in self.rsus.items()
        }
        mapping = {}
        for sid, seg in self.segments.items():
            zone = seg.get("v2x_zone", "")
            mapping[sid] = zone_to_rsu.get(zone, "RSU-01")
        return mapping

    def update_from_events(self, events: List[Dict[str, Any]]) -> None:
        """Update weather station readings and RSU health from incoming events."""
        for ev in events:
            ev_type = ev.get("event_type")

            # 1. Update weather conditions
            if ev_type == "WEATHER_OBSERVATION":
                st_id = ev.get("station_id") or ev.get("source_id")
                if st_id and st_id in self.weather_by_station:
                    wx = self.weather_by_station[st_id]
                    if ev.get("visibility_m") is not None:
                        wx["visibility_m"] = ev["visibility_m"]
                    if ev.get("rain_level") is not None:
                        wx["rain_level"] = ev["rain_level"]
                    if ev.get("wind_mps") is not None:
                        wx["wind_mps"] = ev["wind_mps"]
                    if ev.get("precipitation_mm_h") is not None:
                        wx["precipitation_mm_h"] = ev["precipitation_mm_h"]
                    if ev.get("road_surface") is not None:
                        wx["road_surface"] = ev["road_surface"]
                    if ev.get("air_temperature_c") is not None:
                        wx["air_temperature_c"] = ev["air_temperature_c"]

            # 2. Update RSU availability
            elif ev_type == "INFRASTRUCTURE_HEALTH":
                comp_type = ev.get("component_type")
                comp_id = ev.get("component_id")
                if comp_type == "RSU" and comp_id in self.rsu_available:
                    status = ev.get("status", "OK")
                    loss = ev.get("network_packet_loss_pct", 0.0)
                    # Mark unavailable if NO_HEARTBEAT, or heavy packet loss (> 50%)
                    if status == "NO_HEARTBEAT" or loss > 50.0:
                        self.rsu_available[comp_id] = False
                    else:
                        self.rsu_available[comp_id] = True

    def evaluate_vehicle_odd(
        self,
        vehicle_id: str,
        vehicle_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Evaluate a single vehicle against its ODD profile.
        Returns: {odd_status, violation_codes, confidence}
        """
        is_active = vehicle_state.get("is_active", True)
        current_segment = vehicle_state.get("current_segment")

        # Inactive vehicle handling per 06_evaluation_rules.json
        if not is_active or not current_segment:
            return {
                "vehicle_id": vehicle_id,
                "odd_status": "UNKNOWN",
                "violation_codes": [],
                "confidence": 1.0,
            }

        # Look up vehicle static attributes
        v_catalog = self.vehicles.get(vehicle_id, {})
        profile_id = vehicle_state.get("odd_profile_id") or v_catalog.get("odd_profile_id", "ODD-A")
        mass_t = vehicle_state.get("gross_mass_t") or v_catalog.get("gross_mass_t", 25.0)

        profile = self.odd_profiles.get(profile_id, {})
        min_visibility = profile.get("min_visibility_m", 70.0)
        v2x_required = profile.get("v2x_required", False)
        max_rain = profile.get("max_rain_level", 3)
        min_gnss = profile.get("min_gnss_quality", 0.55)
        max_map_age = profile.get("max_map_age_min", 1440.0)
        allowed_structures = set(profile.get("allowed_structures", ["open", "bridge", "tunnel"]))

        # Segment attributes
        seg = self.segments.get(current_segment, {})
        structure = seg.get("structure", "open")
        weight_limit = seg.get("weight_limit_t", 44.0)

        # Environmental conditions on this segment
        st_id = self.segment_to_weather.get(current_segment, "WX-01")
        wx = self.weather_by_station.get(st_id, {})
        current_vis = wx.get("visibility_m", 1000.0)
        current_rain = wx.get("rain_level", 0)

        # V2X status on this segment
        rsu_id = self.segment_to_rsu.get(current_segment, "RSU-01")
        v2x_is_ok = self.rsu_available.get(rsu_id, True)

        # Vehicle telemetry values
        gnss_q = vehicle_state.get("gnss_quality")
        map_age = vehicle_state.get("map_age_min")

        violations: List[str] = []

        # 1. Visibility check
        if current_vis < min_visibility:
            violations.append("VISIBILITY")

        # 2. Rain intensity check
        if current_rain > max_rain:
            violations.append("RAIN")

        # 3. GNSS quality check
        if gnss_q is not None and gnss_q < min_gnss:
            violations.append("GNSS")

        # 4. V2X availability check
        if v2x_required and not v2x_is_ok:
            violations.append("V2X")

        # 5. Structure restriction check (e.g. tunnel for ODD-C)
        if structure not in allowed_structures:
            violations.append("STRUCTURE")

        # 6. Weight limit check
        if mass_t > weight_limit:
            violations.append("WEIGHT_LIMIT")

        # 7. Map age check
        if map_age is not None and map_age > max_map_age:
            violations.append("MAP_AGE")

        if violations:
            return {
                "vehicle_id": vehicle_id,
                "odd_status": "VIOLATED",
                "violation_codes": sorted(list(set(violations))),
                "confidence": 0.98,
            }

        return {
            "vehicle_id": vehicle_id,
            "odd_status": "COMPLIANT",
            "violation_codes": [],
            "confidence": 1.0,
        }

    def evaluate_all(
        self,
        vehicles_state: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Evaluate ODD compliance for all 72 vehicles in order."""
        results = []
        for vid in self.ref.vehicle_ids:
            v_data = vehicles_state.get(vid, {})
            assessment = self.evaluate_vehicle_odd(vid, v_data)
            results.append(assessment)
        return results
