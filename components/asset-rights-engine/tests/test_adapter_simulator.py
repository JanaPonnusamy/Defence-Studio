import unittest
from unittest.mock import patch
from asset_rights_engine.simulator import run_simulation


class SimulatorTests(unittest.TestCase):
    def test_all_scenarios(self):
        report = run_simulation()
        self.assertEqual(len(report.scenarios), 18)
        self.assertTrue(all(passed for _, passed in report.scenarios))
        self.assertEqual(report.authentication, "NOT_IMPLEMENTED_SYNTHETIC_IDENTIFIERS_ONLY")
        self.assertEqual(report.storage, "TEMPORARY_DATABASE_DELETED_ON_EXIT")
        self.assertEqual(report.schema_version, 1)
    def test_deterministic_report(self):
        self.assertEqual(run_simulation(), run_simulation())
    def test_no_network_required(self):
        def denied(*args, **kwargs): raise AssertionError("Network prohibited")
        with patch("socket.socket", side_effect=denied), patch("socket.create_connection", side_effect=denied):
            self.assertTrue(all(passed for _, passed in run_simulation().scenarios))

if __name__ == "__main__": unittest.main()
