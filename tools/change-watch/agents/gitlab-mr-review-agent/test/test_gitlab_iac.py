"""IaC の差分検査と投稿のテスト。GitLab API はモックで代替する。"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "gitlab_iac.py"
spec = importlib.util.spec_from_file_location("gitlab_iac", PLUGIN)
iac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iac)

TERRAFORM = """@@ -1,7 +1,9 @@
 resource "aws_security_group" "web" {
   ingress {
     cidr_blocks = [
       "10.0.0.0/8",
+      "0.0.0.0/0",
+      "::/0",
     ]
   }
 }
"""
CLOUDFORMATION = """@@ -1,7 +1,8 @@
 AWSTemplateFormatVersion: "2010-09-09"
 Resources:
   Web:
     Type: AWS::EC2::SecurityGroupIngress
     Properties:
       IpProtocol: tcp
-      CidrIp: 10.0.0.0/8
+      CidrIp: 0.0.0.0/0
+      CidrIpv6: "::/0"
"""
BICEP = """@@ -3,5 +3,5 @@
   properties: {
     access: 'Allow'
     direction: 'Inbound'
-    sourceAddressPrefix: '10.0.0.0/8'
+    sourceAddressPrefix: '0.0.0.0/0'
   }
"""
ARM = """@@ -35,3 +35,4 @@
     "properties": {
       "direction": "Inbound",
+      "sourceAddressPrefixes": ["::/0"],
       "access": "Allow"
"""
IAM = """@@ -1,4 +1,6 @@
 statement {
-  actions = ["s3:GetObject"]
+  actions = ["s3:*"]
+  resources = ["arn:aws:s3:::example/*"]
 }
+policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
"""
STORAGE = """@@ -1,3 +1,4 @@
 resource "azurerm_storage_account" "example" {
-  allow_nested_items_to_be_public = false
+  allow_nested_items_to_be_public = true
+  block_public_policy = false
 }
"""
PRIVATE = """@@ -1,4 +1,5 @@
 ingress {
-  cidr_blocks = ["10.0.0.0/8"]
+  cidr_blocks = ["192.168.0.0/16"]
+  actions = ["s3:GetObject"]
 }
"""
NETWORK = """@@ -10,3 +10,4 @@ resource "aws_vpc" "example" {
   cidr_block = "10.0.0.0/16"
+  enable_dns_support = true
 }
"""
DELETION = """@@ -4,4 +4,3 @@ resource "aws_security_group" "example" {
   ingress {
-    cidr_blocks = ["0.0.0.0/0"]
   }
 }
"""


def change(path, diff):
    return {"new_path": path, "old_path": path, "diff": diff}


class InspectTest(unittest.TestCase):
    def test_rules_and_evidence_lines(self):
        fixtures = [
            ("main.tf", TERRAFORM, [(5, "open-ingress"), (6, "open-ingress")]),
            ("stack.yml", CLOUDFORMATION, [(7, "open-ingress"), (8, "open-ingress")]),
            ("main.bicep", BICEP, [(6, "open-ingress")]),
            ("azuredeploy.json", ARM, [(37, "open-ingress")]),
            ("policy.tf", IAM, [(2, "iam-wildcard"), (3, "iam-wildcard"), (5, "iam-admin")]),
            ("storage.tf", STORAGE, [(2, "public-storage"), (3, "public-storage")]),
            ("vpc.tf", NETWORK, [(11, "network-change")]),
        ]
        for path, diff, expected in fixtures:
            with self.subTest(path=path):
                result = iac.inspect([change(path, diff)], mock.Mock(side_effect=AssertionError))
                self.assertEqual([(row["line"], row["rule"]) for row in result["findings"]], expected)
                level = "low" if path == "vpc.tf" else "high"
                self.assertTrue(all(row["severity"] == level for row in result["findings"]))

    def test_no_high_risk_for_private_outbound_and_comments(self):
        for path, diff in [
            ("private.tf", PRIVATE),
            ("README.md", TERRAFORM),
            ("deleted.tf", DELETION),
            ("comment.tf", TERRAFORM.replace('+      "', '+      # "')),
            ("outbound.tf", TERRAFORM.replace("ingress", "egress")),
            ("outbound.bicep", BICEP.replace("Inbound", "Outbound")),
            ("deny.bicep", BICEP.replace("Allow", "Deny")),
            ("description.tf", '@@ -1,0 +1 @@\n+description = "Action: *"\n'),
        ]:
            with self.subTest(path=path):
                result = iac.inspect([change(path, diff)], mock.Mock(side_effect=AssertionError))
                self.assertFalse([row for row in result["findings"] if row["severity"] == "high"])
                if path not in ("comment.tf", "outbound.tf"):
                    self.assertEqual(result["findings"], [])

    def test_added_key_does_not_report_unchanged_dangerous_value(self):
        diff = """@@ -1,5 +1,5 @@
 ingress {
-  previous_cidrs = [
+  cidr_blocks = [
     "0.0.0.0/0",
   ]
 }
"""
        result = iac.inspect([change("main.tf", diff)], mock.Mock(side_effect=AssertionError))
        self.assertEqual(result["findings"], [])

    def test_template_signatures_outside_hunk_and_unrelated_yaml(self):
        read_file = mock.Mock(side_effect=['{"$schema": "https://example/deploymentTemplate.json"}', 'name: workflow'])
        result = iac.inspect([change("app.json", ARM), change("workflow.yml", BICEP)], read_file)
        self.assertEqual([row["format"] for row in result["findings"]], ["arm"])
        self.assertEqual(read_file.call_args_list, [mock.call("app.json"), mock.call("workflow.yml")])

    def test_yaml_iam_block_list(self):
        diff = """@@ -10,3 +10,4 @@
           Action:
             - s3:GetObject
+            - 's3:*'
           Resource: 'arn:aws:s3:::bucket'
"""
        result = iac.inspect([change("infra/cloudformation/iam.yaml", diff)], mock.Mock(side_effect=AssertionError))
        self.assertEqual([row["rule"] for row in result["findings"]], ["iam-wildcard"])

    def test_fingerprint_ignores_line_shifts_but_detects_changed_diff(self):
        def fingerprint(diff):
            return iac.inspect([change("main.tf", diff)], None)["fingerprint"]
        self.assertEqual(fingerprint(TERRAFORM), fingerprint(TERRAFORM.replace("+1,9", "+11,9")))
        self.assertNotEqual(fingerprint(TERRAFORM), fingerprint(TERRAFORM.replace("+1,9", "+1,10") + "+# changed\n"))


class CommandTest(unittest.TestCase):
    def run_command(self, command, diffs, env=None, notes=(), output=None):
        gl = mock.Mock()
        gl.mr_diffs.return_value = diffs
        gl.notes.return_value = notes
        stdout, stderr = io.StringIO(), io.StringIO()
        config = {"GITLAB_URL": "https://example.test", "GITLAB_TOKEN": "token", "GITLAB_PROJECT": "g/p"}
        with mock.patch.dict(os.environ, {**config, **(env or {})}, clear=True), \
                mock.patch.object(iac._mr, "GitLab", return_value=gl), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            args = ["--format", "markdown", "--output", str(output)] if output else ["--format", "json"]
            status = iac.main([command, "7", *args])
        self.assertEqual(status, 0, stderr.getvalue())
        gl.mr_diffs.assert_called_once_with("7")
        return gl, stdout.getvalue() if output else json.loads(stdout.getvalue())

    def test_check_output(self):
        diffs = [change("main.tf", TERRAFORM), {"new_path": "large.tf", "too_large": True}]
        gl, result = self.run_command("check", diffs)
        gl.call.assert_not_called()
        self.assertEqual(result["skipped_files"], ["large.tf"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "check.md"
            _, stdout = self.run_command("check", diffs, output=output)
            self.assertEqual(stdout, "")
            self.assertEqual(output.read_text(encoding="utf-8"), iac.markdown(result) + "\n")

    def test_dry_run_and_no_findings(self):
        gl, result = self.run_command("post", [change("main.tf", TERRAFORM)])
        self.assertEqual(result["post_status"], "dry-run")
        self.assertEqual(result["label"], "needs-extra-approval")
        gl.call.assert_not_called()
        gl.notes.assert_not_called()
        gl, result = self.run_command("post", [change("main.tf", DELETION)], {"MR_REVIEW_IAC_POST": "1"})
        self.assertEqual(result["post_status"], "no-findings")
        gl.call.assert_not_called()
        gl.notes.assert_not_called()

    def test_post_and_duplicate(self):
        diffs = [change("main.tf", TERRAFORM)]
        env = {"MR_REVIEW_IAC_POST": "1", "MR_REVIEW_IAC_LABEL": "review-iac"}
        gl, result = self.run_command("post", diffs, env)
        body = iac.markdown(result)
        self.assertEqual(result["post_status"], "posted")
        self.assertEqual(gl.call.call_args_list, [
            mock.call("PUT", "/merge_requests/7", body={"add_labels": "review-iac"}),
            mock.call("POST", "/merge_requests/7/notes", body={"body": body}),
        ])
        self.assertIn(iac.marker(result), body.splitlines())
        gl, result = self.run_command("post", diffs, env, notes=[{"body": body}])
        self.assertEqual(result["post_status"], "duplicate")
        gl.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
