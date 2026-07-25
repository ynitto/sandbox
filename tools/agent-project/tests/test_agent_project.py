"""Compatibility entry point for tests moved into focused modules."""

import unittest

from tests.test_autonomy import TestLoopEngineering
from tests.test_backlog import TestIntake


if __name__ == "__main__":
    unittest.main()
