"""Unit and integration test suite for VUPSEN Squad corridor control center."""

import gzip
import io
import json
import unittest
import zipfile
from pathlib import Path

import jsonschema

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from src.config import ReferenceData, load_reference_data
from src.core.state import SystemState


class TestReferenceData(unittest.TestCase):
    """Test loading and integrity of static reference catalogs."""

    @classmethod
    def setUpClass(cls):
        cls.ref = load_reference_data()

    def test_segments_count_and_properties(self):
        self.assertEqual(len(self.ref.segments), 86, "Network must have exactly 86 segments")
        self.assertEqual(len(self.ref.segment_ids), 86)
        for sid in self.ref.segment_ids:
            self.assertRegex(sid, r"^S\d{3}$", f"Invalid segment ID format: {sid}")
            seg = self.ref.segments[sid]
            self.assertIn("length_m", seg)
            self.assertIn("speed_limit_kmh", seg)
            self.assertIn("weight_limit_t", seg)
            self.assertGreater(seg["length_m"], 0)

    def test_sources_count_and_types(self):
        self.assertEqual(len(self.ref.sources), 50, "Registry must have exactly 50 sources")
        self.assertEqual(len(self.ref.source_ids), 50)
        for sid in self.ref.source_ids:
            src = self.ref.sources[sid]
            self.assertIn("source_type", src)

    def test_vehicles_count_and_profiles(self):
        self.assertEqual(len(self.ref.vehicles), 72, "Fleet must contain exactly 72 vehicles")
        self.assertEqual(len(self.ref.vehicle_ids), 72)
        valid_profiles = {"ODD-A", "ODD-B", "ODD-C", "ODD-D"}
        for vid in self.ref.vehicle_ids:
            self.assertRegex(vid, r"^AV-\d{3}$", f"Invalid vehicle ID format: {vid}")
            v = self.ref.vehicles[vid]
            self.assertIn(v["odd_profile_id"], valid_profiles)

    def test_safe_stops_and_hubs(self):
        self.assertEqual(len(self.ref.safe_stops), 10, "Must have exactly 10 safe stops")
        self.assertEqual(len(self.ref.hubs), 4, "Must have exactly 4 hubs")
        self.assertEqual(self.ref.max_remote_sessions, 6, "Remote support pool limit must be 6")


class TestDecisionSchemaCompliance(unittest.TestCase):
    """Test that all decision snapshots strictly adhere to 03_decision.schema.json."""

    @classmethod
    def setUpClass(cls):
        cls.ref = load_reference_data()
        schema_path = PROJECT_ROOT / "data" / "contract" / "03_decision.schema.json"
        with open(schema_path, "r", encoding="utf-8") as f:
            cls.schema = json.load(f)
        cls.validator = jsonschema.Draft202012Validator(cls.schema)

    def test_baseline_snapshot_schema_validity(self):
        state = SystemState(ref=self.ref)
        dummy_packet = {
            "scenario_id": "TEST-001",
            "packet_id": "TEST-001-P0001",
            "step": 1,
            "decision_time": "2026-09-28T08:00:05.000Z",
            "events": [],
        }
        decision = state.process_packet(dummy_packet)
        errors = list(self.validator.iter_errors(decision))
        self.assertEqual(errors, [], f"Schema validation errors found: {[e.message for e in errors]}")

    def test_real_packet_processing_and_telemetry_update(self):
        state = SystemState(ref=self.ref)
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_packets.ndjson"
        zip_path = Path("/home/vip/конкурс/Беспилотный_коридор.zip")

        packet = None
        if fixture_path.exists():
            with open(fixture_path, "r", encoding="utf-8") as f:
                packet = json.loads(f.readline().strip())
        elif zip_path.exists():
            with zipfile.ZipFile(zip_path) as outer:
                with outer.open("03_Данные_Беспилотный_коридор.zip") as inner_file:
                    with zipfile.ZipFile(io.BytesIO(inner_file.read())) as inner:
                        with inner.open("02_train/TRAIN-001/packets.ndjson.gz") as p_file:
                            with gzip.GzipFile(fileobj=p_file) as gz:
                                packet = json.loads(gz.readline().decode("utf-8").strip())
        else:
            self.skipTest("No packet source available for integration test")

        self.assertIsNotNone(packet)
        decision = state.process_packet(packet)

        # Validate decision structure and counts
        errors = list(self.validator.iter_errors(decision))
        self.assertEqual(errors, [], f"Validation errors on real packet: {[e.message for e in errors]}")
        self.assertEqual(len(decision["state_estimates"]), 86)
        self.assertEqual(len(decision["source_assessments"]), 50)
        self.assertEqual(len(decision["vehicle_assessments"]), 72)
        self.assertEqual(len(decision["vehicle_actions"]), 72)


if __name__ == "__main__":
    unittest.main()
