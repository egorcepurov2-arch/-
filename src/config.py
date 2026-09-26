"""Configuration and reference data loader for VUPSEN Squad autonomous corridor system."""

import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def get_project_root() -> Path:
    """Return project root directory."""
    return Path(__file__).resolve().parent.parent


def get_reference_dir() -> Path:
    """
    Locate reference directory with prioritized fallbacks:
    1. Environment variable REFERENCE_DIR
    2. Local ./data/reference relative to project root
    3. Docker default /data/reference
    """
    if "REFERENCE_DIR" in os.environ:
        p = Path(os.environ["REFERENCE_DIR"])
        if p.exists():
            return p

    local_ref = get_project_root() / "data" / "reference"
    if local_ref.exists():
        return local_ref

    docker_ref = Path("/data/reference")
    if docker_ref.exists():
        return docker_ref

    return local_ref


def get_contract_dir() -> Path:
    """Locate contract directory."""
    if "CONTRACT_DIR" in os.environ:
        p = Path(os.environ["CONTRACT_DIR"])
        if p.exists():
            return p

    local_contract = get_project_root() / "data" / "contract"
    if local_contract.exists():
        return local_contract

    docker_contract = Path("/data/contract")
    if docker_contract.exists():
        return docker_contract

    return local_contract


@dataclass
class ReferenceData:
    """Preloaded, immutable static reference datasets."""
    # Collections
    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    segments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    segment_ids: List[str] = field(default_factory=list)
    hubs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    safe_stops: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    rsu_catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sensor_catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    weather_stations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    odd_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    vehicles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    vehicle_ids: List[str] = field(default_factory=list)
    transport_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    remote_support_pool: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    source_ids: List[str] = field(default_factory=list)

    # Fast Lookups
    max_remote_sessions: int = 6


def load_csv(file_path: Path) -> List[Dict[str, str]]:
    """Helper to load a CSV file into list of dicts."""
    if not file_path.exists():
        print(f"[WARN] Reference file missing: {file_path}", file=sys.stderr)
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(file_path: Path) -> Any:
    """Helper to load a JSON file."""
    if not file_path.exists():
        print(f"[WARN] Reference file missing: {file_path}", file=sys.stderr)
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_reference_data(ref_dir: Optional[Path] = None) -> ReferenceData:
    """Load all 13 reference datasets into a unified ReferenceData instance."""
    if ref_dir is None:
        ref_dir = get_reference_dir()

    data = ReferenceData()

    # 1. Nodes
    for row in load_csv(ref_dir / "01_network_nodes.csv"):
        data.nodes[row["node_id"]] = {
            "node_id": row["node_id"],
            "name": row.get("name", ""),
            "kind": row.get("kind", ""),
            "x_m": float(row.get("x_m", 0.0)),
            "y_m": float(row.get("y_m", 0.0)),
        }

    # 2. Segments (86 expected)
    for row in load_csv(ref_dir / "02_network_segments.csv"):
        seg_id = row["segment_id"]
        data.segments[seg_id] = {
            "segment_id": seg_id,
            "from_node": row["from_node"],
            "to_node": row["to_node"],
            "length_m": float(row.get("length_m", 0.0)),
            "lanes": int(row.get("lanes", 1)),
            "speed_limit_kmh": float(row.get("speed_limit_kmh", 90.0)),
            "capacity_vph": float(row.get("capacity_vph", 1000.0)),
            "road_group": row.get("road_group", ""),
            "direction": row.get("direction", "ONE_WAY"),
            "road_class": row.get("road_class", ""),
            "v2x_zone": row.get("v2x_zone", ""),
            "structure": row.get("structure", "OPEN_ROAD"),
            "weight_limit_t": float(row.get("weight_limit_t", 44.0)),
            "av_allowed": row.get("av_allowed", "true").lower() in ("true", "1", "yes"),
            "base_flow_vph": float(row.get("base_flow_vph", 0.0)),
        }
    data.segment_ids = sorted(data.segments.keys())

    # 3. Hubs
    for row in load_csv(ref_dir / "04_hubs.csv"):
        data.hubs[row["hub_id"]] = {
            "hub_id": row["hub_id"],
            "node_id": row["node_id"],
            "name": row.get("name", ""),
            "capacity_vehicles": int(row.get("capacity_vehicles", 20)),
            "service_rate_vph": float(row.get("service_rate_vph", 10.0)),
            "x_m": float(row.get("x_m", 0.0)),
            "y_m": float(row.get("y_m", 0.0)),
        }

    # 4. Safe Stops (10 expected)
    for row in load_csv(ref_dir / "05_safe_stops.csv"):
        stop_id = row["safe_stop_id"]
        data.safe_stops[stop_id] = {
            "safe_stop_id": stop_id,
            "segment_id": row["segment_id"],
            "capacity_vehicles": int(row.get("capacity_vehicles", 2)),
            "has_remote_link": row.get("has_remote_link", "true").lower() in ("true", "1", "yes"),
            "max_vehicle_mass_t": float(row.get("max_vehicle_mass_t", 44.0)),
            "name": row.get("name", ""),
        }

    # 5. RSU Catalog (13 expected)
    for row in load_csv(ref_dir / "06_rsu_catalog.csv"):
        data.rsu_catalog[row["rsu_id"]] = {
            "rsu_id": row["rsu_id"],
            "v2x_zone": row.get("v2x_zone", ""),
            "covered_segments": [s.strip() for s in row.get("covered_segments", "").split(";") if s.strip()],
            "protocol_profile": row.get("protocol_profile", ""),
            "nominal_latency_ms": float(row.get("nominal_latency_ms", 20.0)),
        }

    # 6. Sensor Catalog
    for row in load_csv(ref_dir / "07_sensor_catalog.csv"):
        data.sensor_catalog[row["source_id"]] = {
            "source_id": row["source_id"],
            "source_type": row.get("source_type", ""),
            "segment_id": row.get("segment_id", ""),
            "sampling_period_sec": float(row.get("sampling_period_sec", 5.0)),
            "nominal_accuracy": float(row.get("nominal_accuracy", 0.9)),
        }

    # 7. Weather Stations (8 expected)
    for row in load_csv(ref_dir / "08_weather_stations.csv"):
        data.weather_stations[row["station_id"]] = {
            "station_id": row["station_id"],
            "node_id": row.get("node_id", ""),
            "x_m": float(row.get("x_m", 0.0)),
            "y_m": float(row.get("y_m", 0.0)),
            "sampling_period_sec": float(row.get("sampling_period_sec", 5.0)),
            "coverage_radius_m": float(row.get("coverage_radius_m", 15000.0)),
        }

    # 8. ODD Profiles (JSON)
    data.odd_profiles = load_json(ref_dir / "09_odd_profiles.json")

    # 9. Vehicles (72 expected)
    for row in load_csv(ref_dir / "10_vehicles.csv"):
        vid = row["vehicle_id"]
        data.vehicles[vid] = {
            "vehicle_id": vid,
            "manufacturer_group": row.get("manufacturer_group", ""),
            "odd_profile_id": row.get("odd_profile_id", "ODD-A"),
            "gross_mass_t": float(row.get("gross_mass_t", 20.0)),
            "length_m": float(row.get("length_m", 16.5)),
            "origin_hub_id": row.get("origin_hub_id", ""),
            "destination_hub_id": row.get("destination_hub_id", ""),
            "cargo_priority": int(row.get("cargo_priority", 1)),
            "onboard_map_version": row.get("onboard_map_version", ""),
            "nominal_max_speed_kmh": float(row.get("nominal_max_speed_kmh", 90.0)),
        }
    data.vehicle_ids = sorted(data.vehicles.keys())

    # 10. Transport Orders
    for row in load_csv(ref_dir / "11_transport_orders.csv"):
        data.transport_orders[row["order_id"]] = {
            "order_id": row["order_id"],
            "vehicle_id": row.get("vehicle_id", ""),
            "origin_hub_id": row.get("origin_hub_id", ""),
            "destination_hub_id": row.get("destination_hub_id", ""),
            "cargo_class": row.get("cargo_class", "GENERAL"),
            "priority": int(row.get("priority", 1)),
            "deadline_offset_min": float(row.get("deadline_offset_min", 120.0)),
            "penalty_per_min": float(row.get("penalty_per_min", 1.0)),
        }

    # 11. Remote Support Pool (JSON, max 6)
    pool_cfg = load_json(ref_dir / "12_remote_support_pool.json")
    data.remote_support_pool = pool_cfg
    if isinstance(pool_cfg, dict):
        data.max_remote_sessions = pool_cfg.get("max_concurrent_sessions", 6)

    # 12. Sources (50 expected)
    for row in load_csv(ref_dir / "13_source_registry.csv"):
        sid = row["source_id"]
        data.sources[sid] = {
            "source_id": sid,
            "source_type": row.get("source_type", ""),
            "expected_period_sec": float(row.get("expected_period_sec", 5.0)),
            "coverage": row.get("coverage", ""),
        }
    data.source_ids = sorted(data.sources.keys())

    return data
