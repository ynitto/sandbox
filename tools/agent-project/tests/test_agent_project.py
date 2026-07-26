"""Compatibility entry point for tests moved into focused modules."""

import unittest

if __name__ == "__main__":
    from test_autonomy import TestLoopEngineering
    from test_backlog import TestIntake

    unittest.main()
