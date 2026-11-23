import codecs
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "csv_peek.py"


class CsvPeekTest(unittest.TestCase):
    def run_cli(self, data, *options, stdin=False):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.csv"
            if not stdin:
                source.write_bytes(data)
            return subprocess.run(
                [sys.executable, str(CLI), "-" if stdin else str(source), *options],
                input=data if stdin else b"",
                capture_output=True,
                encoding=None,
                check=False,
            )

    def summary(self, data, *options, stdin=False):
        result = self.run_cli(data, "--json", *options, stdin=stdin)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout), result.stderr.decode()

    def test_basic_json(self):
        summary, _ = self.summary(b"name,score\nAlice,10\nBob,20\n")
        self.assertEqual(summary["row_count"], 2)
        self.assertEqual(summary["column_count"], 2)
        self.assertEqual(summary["headers"], ["name", "score"])
        self.assertEqual(summary["encoding"], "utf-8")
        self.assertEqual(summary["delimiter"], ",")
        self.assertIsNone(summary["limit"])
        self.assertEqual(summary["uneven_row_count"], 0)
        self.assertEqual(summary["sample_rows"], [["Alice", "10"], ["Bob", "20"]])
        self.assertEqual(
            summary["columns"][1],
            {"name": "score", "type": "integer", "empty_count": 0,
             "unique_count": 2, "min": 10, "max": 20},
        )

    def test_types_and_counts(self):
        data = (
            b"score,date,name,flag,sparse,mixed\n"
            b"1,2025-01-01,Alice,True, ,word\n"
            b"2.5,2025-01-02,Bob,NO,,1\n"
            b"1,2025-01-03, Alice ,yes,9,\n"
        )
        summary, _ = self.summary(data)
        columns = summary["columns"]
        self.assertEqual(
            [column["type"] for column in columns],
            ["decimal", "datetime", "string", "boolean", "integer", "mixed"],
        )
        self.assertEqual((columns[0]["min"], columns[0]["max"]), (1, 2.5))
        self.assertEqual(columns[2]["unique_count"], 2)
        self.assertEqual(columns[4]["empty_count"], 2)
        self.assertEqual(columns[4]["unique_count"], 1)

    def test_shift_jis_from_file_and_stdin(self):
        data = "name,city\n太郎,東京\n".encode("shift_jis")
        for stdin in (False, True):
            with self.subTest(stdin=stdin):
                summary, _ = self.summary(data, stdin=stdin)
                self.assertEqual(summary["encoding"], "shift_jis")
                self.assertEqual(summary["sample_rows"], [["太郎", "東京"]])

    def test_utf8_bom_and_tab_inference(self):
        summary, _ = self.summary(codecs.BOM_UTF8 + "名前\t点数\n葵\t8\n".encode())
        self.assertEqual(summary["encoding"], "utf-8-sig")
        self.assertEqual(summary["delimiter"], "\t")
        self.assertEqual(summary["headers"], ["名前", "点数"])

    def test_cp932_from_file_and_stdin(self):
        data = "name,city\n髙橋,東京\n".encode("cp932")
        with self.assertRaises(UnicodeDecodeError):
            data.decode("shift_jis")
        for stdin in (False, True):
            with self.subTest(stdin=stdin):
                summary, _ = self.summary(data, stdin=stdin)
                self.assertEqual(summary["encoding"], "cp932")
                self.assertEqual(summary["sample_rows"], [["髙橋", "東京"]])

    def test_limit_ignores_later_malformed_input(self):
        data = b'a,b\n1,2\n3,4\n"\xff\n'
        summary, _ = self.summary(data, "--limit", "2", "--sample", "1")
        self.assertEqual(summary["row_count"], 2)
        self.assertEqual(summary["limit"], 2)
        self.assertEqual(summary["encoding"], "utf-8")
        self.assertEqual(summary["sample_rows"], [["1", "2"]])
        output = self.run_cli(data, "--limit", "2")
        self.assertEqual(output.returncode, 0)
        self.assertIn("先頭 2 行時点の概算", output.stdout.decode())

    def test_uneven_rows(self):
        summary, stderr = self.summary(b"a,b\n1\n2,3,4\n5,6\n")
        self.assertIn("列数が揃っていない行: 2 件", stderr)
        self.assertEqual(summary["uneven_row_count"], 2)
        self.assertEqual(summary["headers"], ["a", "b", "col3"])
        self.assertEqual(summary["sample_rows"], [["1", "", ""], ["2", "3", "4"], ["5", "6", ""]])
        self.assertEqual([col["empty_count"] for col in summary["columns"]], [0, 1, 2])

    def test_explicit_options_without_header(self):
        data = '"青\n赤"|1\n白|0\n'.encode("utf-16")
        summary, _ = self.summary(
            data, "--delimiter", "|", "--encoding", "utf-16", "--no-header", stdin=True,
        )
        self.assertEqual(summary["headers"], ["col1", "col2"])
        self.assertEqual(summary["row_count"], 2)
        self.assertEqual(summary["sample_rows"], [["青\n赤", "1"], ["白", "0"]])

    def test_input_errors(self):
        for data in (b"a,b\n\x81,1\n", b'a,b\n"open,1\n'):
            with self.subTest(data=data):
                result = self.run_cli(data)
                self.assertEqual(result.returncode, 2)
                self.assertIn("エラー", result.stderr.decode())
                self.assertEqual(result.stdout, b"")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "missing.csv"
            result = subprocess.run(
                [sys.executable, str(CLI), str(source)], capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("エラー", result.stderr.decode())
        self.assertIn("missing.csv", result.stderr.decode())
        self.assertEqual(result.stdout, b"")

    def test_output_encoding_error(self):
        for options in ([], ["--json"]):
            with self.subTest(options=options):
                result = subprocess.run(
                    [sys.executable, str(CLI), "-", *options],
                    input="名前\n髙橋\n".encode(),
                    env={**os.environ, "PYTHONIOENCODING": "ascii"},
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"ascii", result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_broken_output_pipe(self):
        reader, writer = os.pipe()
        os.close(reader)
        with os.fdopen(writer, "wb") as output:
            result = subprocess.run(
                [sys.executable, str(CLI), "-", "--json"],
                input=b"name\nAlice\n",
                stdout=output,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("出力できません", result.stderr.decode())
        self.assertNotIn(b"Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
