"""VehicleSpec — 제원 파일 로드, 플래그 덮어쓰기, 섹션 용량·엔진 반영."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ufast.cosim.vehicle import (VehicleSpec, load_vehicle_spec,  # noqa: E402
                                 spec_from_args, DEFAULT_SPEC_FILE)


class TestVehicleSpec(unittest.TestCase):
    def test_default_file_matches_smat2022_vehicle(self):
        self.assertTrue(os.path.exists(DEFAULT_SPEC_FILE))
        spec = load_vehicle_spec()
        self.assertEqual(spec.length_mm, 784.0)
        self.assertEqual(spec.headway_mm, 125.0)
        self.assertEqual(spec.footprint_mm, 909.0)
        self.assertEqual(spec.max_speed_mm_s, 5000.0)
        self.assertEqual(spec.accel_mm_s2, 2000.0)
        self.assertEqual(spec.decel_mm_s2, 3500.0)
        self.assertEqual(spec.line_speed_mm_s, 5000.0)
        self.assertEqual(spec.curve_speed_mm_s, 1000.0)
        self.assertEqual(spec.source, 'vehicle_spec.json')

    def test_default_file_agrees_with_smat2022_csv(self):
        csv_path = os.path.join(ROOT, 'dataset', 'SMAT2022', 'VehicleType.csv')
        from_csv = load_vehicle_spec(csv_path)
        from_json = load_vehicle_spec()
        for k in ('length_mm', 'headway_mm', 'max_speed_mm_s',
                  'accel_mm_s2', 'decel_mm_s2'):
            self.assertEqual(getattr(from_csv, k), getattr(from_json, k), k)

    def test_flags_override_file_with_unit_conversion(self):
        args = SimpleNamespace(vehicle_spec=None, oht_speed=3.0, oht_accel=None,
                               oht_decel=1.5, oht_length=1000.0,
                               oht_headway=None, line_speed=None,
                               curve_speed=0.5)
        spec = spec_from_args(args)
        self.assertEqual(spec.max_speed_mm_s, 3000.0)     # m/s → mm/s
        self.assertEqual(spec.accel_mm_s2, 2000.0)        # untouched
        self.assertEqual(spec.decel_mm_s2, 1500.0)
        self.assertEqual(spec.length_mm, 1000.0)
        self.assertEqual(spec.footprint_mm, 1125.0)
        self.assertEqual(spec.curve_speed_mm_s, 500.0)
        self.assertIn('flags', spec.source)

    def test_custom_json_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'v.json')
            with open(p, 'w') as f:
                json.dump({'length_mm': 900, 'max_speed_mm_s': 4000}, f)
            spec = load_vehicle_spec(p)
            self.assertEqual(spec.length_mm, 900.0)
            self.assertEqual(spec.max_speed_mm_s, 4000.0)
            self.assertEqual(spec.headway_mm, 125.0)   # fallback to built-in
            self.assertEqual(spec.source, 'v.json')

    def test_meta_and_kinematics(self):
        spec = VehicleSpec()
        meta = spec.as_meta()
        self.assertEqual(meta['footprint_mm'], 909.0)
        kin = spec.kinematics()
        self.assertEqual(kin.max_speed, 5000.0)
        self.assertEqual(kin.acceleration, 2000.0)
        self.assertEqual(kin.deceleration, 3500.0)


if __name__ == '__main__':
    unittest.main()
