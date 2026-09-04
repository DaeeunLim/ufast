"""ufast.common.consistency — 입력 정합성 검사 단위 테스트."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ufast.common.consistency import (ConsistencyError, check_fromto,
                                      check_production_families)


class TestCheckFromto(unittest.TestCase):
    def test_all_matched(self):
        data = [('EQ_A', 'EQ_B', 10.0), ('EQ_B', 'EQ_A', 5.0)]
        report = check_fromto(data, {'EQ_A', 'EQ_B'})
        self.assertTrue(report.ok)
        self.assertTrue(report.total == 2 and report.valid == 2)
        self.assertIn('OK', report.summary())
        report.raise_if_invalid()  # no-op

    def test_unknown_equipment_counted(self):
        data = [('EQ_A', 'EQ_X', 10.0),   # to 미매칭
                ('EQ_X', 'EQ_Y', 5.0),    # 양쪽 미매칭
                ('EQ_A', 'EQ_B', 1.0)]    # 정상
        report = check_fromto(data, {'EQ_A', 'EQ_B'})
        self.assertFalse(report.ok)
        self.assertTrue(report.total == 3 and report.valid == 1)
        self.assertEqual(report.unknown['EQ_X'], 2)
        self.assertEqual(report.unknown['EQ_Y'], 1)
        self.assertIn('EQ_X', report.summary())

    def test_strict_raises(self):
        report = check_fromto([('EQ_A', 'EQ_X', 1.0)], {'EQ_A'})
        with self.assertRaises(ConsistencyError):
            report.raise_if_invalid()

    def test_empty_data(self):
        report = check_fromto([], {'EQ_A'})
        self.assertTrue(report.ok and report.total == 0)


class TestCheckProductionFamilies(unittest.TestCase):
    def test_resolved_via_eq_to_node(self):
        report = check_production_families(
            ['FAM_A', 'FAM_B'], {'FAM_A': 'n1', 'FAM_B': 'n2'})
        self.assertTrue(report.ok)

    def test_case_insensitive_node_match(self):
        # UFastInstance.family_node 의 대소문자 무시 폴백과 동일 기준
        report = check_production_families(['fam_a'], {'FAM_A': 'n1'})
        self.assertTrue(report.ok)

    def test_resolved_via_equipment_csv(self):
        me = {'FAM_C': [{'equipment_id': 'C_01', 'node': 'n3'}]}
        report = check_production_families(['FAM_C'], {}, me)
        self.assertTrue(report.ok)

    def test_empty_equipment_pool_not_resolved(self):
        # Equipment.csv 에 family 키만 있고 장비가 없으면 목적지 해석 불가
        report = check_production_families(['FAM_C'], {}, {'FAM_C': []})
        self.assertFalse(report.ok)

    def test_unresolved_family(self):
        report = check_production_families(
            ['FAM_A', 'FAM_MISSING'], {'FAM_A': 'n1'})
        self.assertFalse(report.ok)
        self.assertEqual(report.valid, 1)
        self.assertIn('FAM_MISSING', report.unknown)
        with self.assertRaises(ConsistencyError):
            report.raise_if_invalid()


if __name__ == '__main__':
    unittest.main()
