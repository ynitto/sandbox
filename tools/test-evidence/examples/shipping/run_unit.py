"""Python標準のunittestからJUnit XMLを得る、プロジェクト側の変換例。"""
import sys
import unittest
import xml.etree.ElementTree as ET


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xml = ET.Element("testsuite")

    def record(self, test, status=None, message=""):
        item = ET.SubElement(self.xml, "testcase", classname=type(test).__name__, name=test._testMethodName)
        if status:
            ET.SubElement(item, status).text = message

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, "failure", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, "skipped", reason)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    result = unittest.TextTestRunner(stream=sys.stderr, resultclass=Result).run(suite)
    print(ET.tostring(result.xml, encoding="unicode"))
    raise SystemExit(0 if result.wasSuccessful() else 1)
