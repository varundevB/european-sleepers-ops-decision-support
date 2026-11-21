"""Synthetic checks for the source distinctions that affect planning decisions."""

from types import SimpleNamespace
import unittest

from import_data import fill_key, is_weekly_summary, normalize_code, _running_value


def cell(value=None, formula=False):
    return SimpleNamespace(value=value, data_type="f" if formula else "n")


class ImportInterpretationTests(unittest.TestCase):
    def test_whole_number_codes_do_not_become_different_states(self):
        self.assertEqual(normalize_code(4.0), "4")
        self.assertEqual(normalize_code(-1.0), "-1")
        self.assertEqual(normalize_code(0.0), "0")
        self.assertIsNone(normalize_code(None))

    def test_theme_tint_is_part_of_operational_status(self):
        def themed(tint):
            return SimpleNamespace(fill=SimpleNamespace(
                fill_type="solid",
                fgColor=SimpleNamespace(type="theme", theme=4, tint=tint),
            ))
        self.assertNotEqual(fill_key(themed(0)), fill_key(themed(0.4)))

    def test_weekly_summary_does_not_enter_daily_passenger_totals(self):
        row = [cell() for _ in range(9)]
        row[4] = cell("=AVERAGE(E4:E9)", formula=True)
        self.assertTrue(is_weekly_summary(row, "Milan"))

    def test_recorded_special_service_is_not_dropped_as_weekly_summary(self):
        row = [cell() for _ in range(9)]
        row[2] = cell(17)
        row[4] = cell(120)
        self.assertFalse(is_weekly_summary(row, "Prag"))

    def test_missing_source_pax_does_not_become_cached_zero_cancellations(self):
        self.assertIsNone(_running_value(cell("=E8", True), 0, {"E": None}, 8))

    def test_known_zero_passenger_impact_is_preserved(self):
        self.assertEqual(_running_value(cell("=E8", True), 0, {"E": 0}, 8), 0)

    def test_known_passenger_impact_resolves_from_corrected_pax(self):
        self.assertEqual(_running_value(cell("=E8", True), 0, {"E": 297}, 8), 297)


if __name__ == "__main__":
    unittest.main()
