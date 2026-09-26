#!/usr/bin/env python3
"""
Test runner and JSON schema validator for VUPSEN Squad stream processing.
Extracts sample packets from TRAIN-001 and validates decisions against 03_decision.schema.json.
"""

import gzip
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import jsonschema

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_reference_data
from src.core.state import SystemState


def load_decision_schema() -> dict:
    schema_path = PROJECT_ROOT / "data" / "contract" / "03_decision.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_train_packets(count: int = 5) -> list:
    """Fetch first N packets from TRAIN-001."""
    zip_path = Path("/home/vip/конкурс/Беспилотный_коридор.zip")
    if not zip_path.exists():
        print(f"[ERROR] Data archive not found at {zip_path}", file=sys.stderr)
        return []

    packets = []
    with zipfile.ZipFile(zip_path) as outer:
        with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
            inner_bytes = io.BytesIO(inner_file.read())
            with zipfile.ZipFile(inner_bytes) as inner:
                with inner.open("02_train/TRAIN-001/packets.ndjson.gz") as p_file:
                    with gzip.GzipFile(fileobj=p_file) as gz:
                        for line in gz:
                            line_str = line.decode("utf-8").strip()
                            if line_str:
                                packets.append(json.loads(line_str))
                                if len(packets) >= count:
                                    break
    return packets


def main():
    print("=== [TEST] Validating Baseline Stream Processor ===")
    schema = load_decision_schema()
    validator = jsonschema.Draft202012Validator(schema)

    ref = load_reference_data()
    state = SystemState(ref=ref)

    print("Fetching sample packets from TRAIN-001...")
    packets = get_train_packets(count=10)
    print(f"Loaded {len(packets)} sample packets.")

    total_time = 0.0

    for i, packet in enumerate(packets, start=1):
        t0 = time.perf_counter()
        decision = state.process_packet(packet)
        elapsed = time.perf_counter() - t0
        total_time += elapsed

        # 1. Validate schema
        errors = list(validator.iter_errors(decision))
        if errors:
            print(f"[FAIL] Schema validation error on packet {packet.get('packet_id')}:")
            for err in errors[:5]:
                print(f"  - {err.message} (path: {list(err.path)})")
            sys.exit(1)

        # 2. Validate counts
        assert len(decision["state_estimates"]) == 86, "Must contain exactly 86 segment estimates"
        assert len(decision["source_assessments"]) == 50, "Must contain exactly 50 source assessments"
        assert len(decision["vehicle_assessments"]) == 72, "Must contain exactly 72 vehicle assessments"
        assert len(decision["vehicle_actions"]) == 72, "Must contain exactly 72 vehicle actions"

        print(
            f"  [OK] Packet {i:02d} ({packet.get('packet_id')}) processed in {elapsed * 1000.0:.2f} ms "
            f"({len(packet.get('events', []))} events) -> 100% Valid JSON"
        )

    avg_ms = (total_time / len(packets)) * 1000.0
    print(f"\n[SUCCESS] All {len(packets)} packets passed strict schema validation!")
    print(f"Average decision time: {avg_ms:.2f} ms (Budget limit: 2000.00 ms)")
    print(f"Speedup vs limit: {2000.0 / avg_ms:.1f}x faster than required.")


if __name__ == "__main__":
    main()
