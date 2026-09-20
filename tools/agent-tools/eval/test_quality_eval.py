import unittest
import quality_eval as q

class QualityMetricsTests(unittest.TestCase):
    def test_unknown_is_not_a_clear_or_a_detected_problem(self):
        def row(outcome, expected=False, status="ok"):
            return dict(case="E4", arm="evidence", split="holdout", outcome=outcome,
                        expected_clear=expected, status=status)
        report=q.summarize([row("supported"),row("unknown"),row("problem"),row(None,status="request_failure")])
        g=next(g for g in report["groups"] if g["split"]=="holdout" and g["arm"]=="evidence")
        self.assertEqual(g["false_clear_rate"],1/3)
        self.assertEqual(g["problem_detection_rate"],1/3)
        self.assertEqual(g["resolved_rate"],2/3)
        self.assertEqual(g["failures"],1)
        self.assertEqual(report["status"],"insufficient_data")

    def test_all_abstaining_never_looks_like_success(self):
        rows=[dict(case="E4", arm="evidence", split="holdout", outcome="unknown",expected_clear=False,status="ok")]
        g=q.summarize(rows)["groups"][-1]
        self.assertEqual(g["false_clear_rate"],0)
        self.assertEqual(g["problem_detection_rate"],0)
        self.assertIsNone(g["accuracy_resolved"])

    def test_fixture_labels_reuse_existing_checker(self):
        for cid, expected in [("E1",False),("E2",True),("E3",False),("E4",False),("E5",False),("E6",True)]:
            data,label=q.fixture(cid)
            self.assertEqual(label,expected)
            self.assertTrue(all(c in data["prompt"] for c in data["criteria"]))

if __name__ == "__main__": unittest.main()
