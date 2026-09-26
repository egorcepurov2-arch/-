#!/usr/bin/env python3
"""
Main streaming console entrypoint for VUPSEN Squad corridor control center.

Protocol:
- Reads observation packets line-by-line from sys.stdin (NDJSON).
- Processes each 5-second packet within 2.0 seconds.
- Writes exactly one decision snapshot line to sys.stdout (JSON).
- Flushes sys.stdout immediately after writing.
- Emits all diagnostics and debug logs strictly to sys.stderr.
- Terminates with code 0 on EOF.
"""

import json
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path for robust standalone execution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_reference_data
from src.core.state import SystemState


def main() -> None:
    # 1. Warm-up and reference loading
    start_init = time.perf_counter()
    try:
        ref_data = load_reference_data()
        state = SystemState(ref=ref_data)
        init_duration = time.perf_counter() - start_init
        print(
            f"[INIT] VUPSEN Squad runner initialized in {init_duration:.3f}s. "
            f"Loaded {len(ref_data.segments)} segments, {len(ref_data.sources)} sources, "
            f"{len(ref_data.vehicles)} vehicles.",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[CRITICAL] Initialization failed: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Streaming packet loop
    packet_count = 0
    total_processing_time = 0.0

    try:
        for line_num, line in enumerate(sys.stdin, start=1):
            line_str = line.strip()
            if not line_str:
                continue

            step_start = time.perf_counter()

            try:
                packet = json.loads(line_str)
            except json.JSONDecodeError as err:
                print(f"[ERROR] Failed to parse JSON on line {line_num}: {err}", file=sys.stderr)
                continue

            # Process packet
            try:
                decision = state.process_packet(packet)
            except Exception as proc_err:
                print(
                    f"[ERROR] Exception processing packet {packet.get('packet_id')}: {proc_err}",
                    file=sys.stderr,
                )
                # Fallback to minimal snapshot to prevent test timeout
                decision = state.build_decision_snapshot()

            # Output decision snapshot
            out_str = json.dumps(decision, ensure_ascii=False)
            sys.stdout.write(out_str + "\n")
            sys.stdout.flush()

            step_duration = time.perf_counter() - step_start
            packet_count += 1
            total_processing_time += step_duration

            if step_duration > 1.5:
                print(
                    f"[WARN] Step {packet.get('step')} took {step_duration:.3f}s (budget: 2.0s)",
                    file=sys.stderr,
                )

    except KeyboardInterrupt:
        print("\n[INFO] Runner interrupted by user.", file=sys.stderr)
    except Exception as stream_err:
        print(f"[FATAL] Unhandled streaming error: {stream_err}", file=sys.stderr)
        sys.exit(1)

    avg_time = (total_processing_time / packet_count * 1000.0) if packet_count > 0 else 0.0
    print(
        f"[DONE] Completed {packet_count} packets in {total_processing_time:.2f}s "
        f"(avg: {avg_time:.2f} ms/packet).",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
