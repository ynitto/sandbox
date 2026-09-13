import unittest
from datetime import date
from agentcore.loopentry import resolve_date_inputs


class DateInputsTest(unittest.TestCase):
    def test_year_boundary_and_literals(self):
        values = {"month": "@date:previous-month", "day": "@date:yesterday", "literal": "today"}
        self.assertEqual(resolve_date_inputs(values, today=date(2026, 1, 1)),
                         {"month": "2025-12", "day": "2025-12-31", "literal": "today"})
        self.assertEqual(values["month"], "@date:previous-month")

    def test_leap_day(self):
        self.assertEqual(resolve_date_inputs({"day": "@date:yesterday"}, today=date(2024, 3, 1)),
                         {"day": "2024-02-29"})

    def test_each_dispatch_uses_its_own_date(self):
        values = {"month": "@date:previous-month"}
        self.assertEqual(resolve_date_inputs(values, today=date(2026, 9, 13))["month"], "2026-08")
        self.assertEqual(resolve_date_inputs(values, today=date(2026, 10, 13))["month"], "2026-09")
