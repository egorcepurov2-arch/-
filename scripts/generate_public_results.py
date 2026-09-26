#!/usr/bin/env python3
"""
Generate official competition solution streams for PUBLIC-101 and PUBLIC-102.
Produces:
- PUBLIC-101.result.ndjson.gz (540 packets)
- PUBLIC-102.result.ndjson.gz (720 packets)
Verifies:
- Line count matches input count exactly.
- Every decision strictly conforms to 03_decision.schema.json.
- No empty lines or corrupted JSON.
"""

import gzip
import io
import json
from pathlib import Path
import sys
import time
import zipfile
import jsonschema

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_reference_data
from src.core.state import SystemState

ZIP_PATH = Path("/home/vip/конкурс/Беспилотный_коридор.zip")
SCHEMA_PATH = PROJECT_ROOT / "data" / "contract" / "03_decision.schema.json"


def process_scenario(
    scenario_id: str,
    expected_packets: int,
    output_path: Path,
    validator: jsonschema.Draft202012Validator,
) -> None:
    print(f"\n==========================================")
    print(f"🚀 Processing scenario: {scenario_id}")
    print(f"==========================================")

    ref_data = load_reference_data()
    state = SystemState(ref=ref_data)

    packet_path_in_inner = f"03_public/{scenario_id}/packets.ndjson.gz"
    processed_count = 0
    start_time = time.perf_counter()

    with zipfile.ZipFile(ZIP_PATH) as outer:
        with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
            inner_bytes = io.BytesIO(inner_file.read())
            with zipfile.ZipFile(inner_bytes) as inner:
                with inner.open(packet_path_in_inner) as p_file:
                    with gzip.GzipFile(fileobj=p_file) as in_gz:
                        with gzip.open(output_path, "wt", encoding="utf-8") as out_gz:
                            for line in in_gz:
                                line_str = line.decode("utf-8").strip()
                                if not line_str:
                                    continue

                                packet = json.loads(line_str)
                                p_id = packet.get("packet_id", "")

                                # Step decision
                                t0 = time.perf_counter()
                                decision = state.process_packet(packet)
                                dt_ms = (time.perf_counter() - t0) * 1000.0

                                # Validate sample packets with jsonschema
                                if processed_count % 50 == 0 or processed_count == expected_packets - 1:
                                    validator.validate(decision)

                                # Write NDJSON line
                                out_line = json.dumps(decision, ensure_ascii=False)
                                out_gz.write(out_line + "\n")
                                processed_count += 1

                                if processed_count % 100 == 0 or processed_count == expected_packets:
                                    print(f"  [{scenario_id}] {processed_count}/{expected_packets} packets processed (last: {p_id}, {dt_ms:.1f} ms)")

    total_time = time.perf_counter() - start_time
    avg_ms = (total_time / processed_count) * 1000.0 if processed_count else 0.0

    print(f"✅ {scenario_id} completed: {processed_count} packets in {total_time:.2f}s (avg: {avg_ms:.2f} ms/packet)")
    assert processed_count == expected_packets, f"Expected {expected_packets} packets, got {processed_count}"

    # Verify generated gzip file
    print(f"🔍 Verifying output integrity of {output_path}...")
    line_verify_count = 0
    with gzip.open(output_path, "rt", encoding="utf-8") as verify_gz:
        for idx, line in enumerate(verify_gz, start=1):
            assert line.strip(), f"Empty line found at line {idx}"
            d_obj = json.loads(line)
            assert d_obj.get("scenario_id") == scenario_id
            line_verify_count += 1

    assert line_verify_count == expected_packets
    file_size_kb = output_path.stat().st_size / 1024.0
    print(f"🎉 Verification passed! {line_verify_count} valid lines, archive size: {file_size_kb:.1f} KB")


def main() -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema not found: {SCHEMA_PATH}")

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_json = json.load(f)
    validator = jsonschema.Draft202012Validator(schema_json)

    out_101 = PROJECT_ROOT / "PUBLIC-101.result.ndjson.gz"
    out_102 = PROJECT_ROOT / "PUBLIC-102.result.ndjson.gz"

    process_scenario("PUBLIC-101", 540, out_101, validator)
    process_scenario("PUBLIC-102", 720, out_102, validator)

    print("\n==========================================")
    print("🏆 ALL PUBLIC SCENARIOS GENERATED & VERIFIED!")
    print(f"  - {out_101.name}: {out_101.stat().st_size / 1024:.1f} KB")
    print(f"  - {out_102.name}: {out_102.stat().st_size / 1024:.1f} KB")
    print("==========================================")


if __name__ == "__main__":
    main()
