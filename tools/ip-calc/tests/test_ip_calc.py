import json
from pathlib import Path
import subprocess
import sys
import unittest


CLI = Path(__file__).resolve().parents[1] / "ip_calc.py"


class IpCalcTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            encoding="utf-8",
            env={"PYTHONIOENCODING": "utf-8"},
        )

    def read_info(self, cidr):
        result = self.run_cli("info", cidr, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_ipv4_info_json(self):
        self.assertEqual(
            self.read_info("192.168.1.10/24"),
            {
                "version": 4,
                "network_address": "192.168.1.0",
                "broadcast_address": "192.168.1.255",
                "netmask": "255.255.255.0",
                "prefix_length": 24,
                "total_addresses": 256,
                "first_usable_host": "192.168.1.1",
                "last_usable_host": "192.168.1.254",
                "usable_host_count": 254,
                "wildcard_mask": "0.0.0.255",
                "is_private": True,
                "is_loopback": False,
                "is_link_local": False,
                "is_multicast": False,
                "is_global": False,
            },
        )

    def test_ipv4_small_networks(self):
        for prefix, count, first in ((31, 2, "192.0.2.0"), (32, 1, "192.0.2.1")):
            with self.subTest(prefix=prefix):
                data = self.read_info(f"192.0.2.1/{prefix}")
                self.assertEqual(data["total_addresses"], count)
                self.assertEqual(data["usable_host_count"], count)
                self.assertEqual(data["first_usable_host"], first)
                self.assertEqual(data["last_usable_host"], "192.0.2.1")

    def test_ipv6_info(self):
        data = self.read_info("2001:db8::/64")
        self.assertEqual(data["version"], 6)
        self.assertEqual(data["network_address"], "2001:db8::")
        self.assertEqual(data["prefix_length"], 64)
        self.assertIsNone(data["broadcast_address"])
        self.assertIsNone(data["netmask"])
        self.assertIsNone(data["wildcard_mask"])
        self.assertEqual(data["total_addresses"], 2**64)
        self.assertEqual(data["usable_host_count"], 2**64)
        self.assertEqual(data["first_usable_host"], "2001:db8::")
        self.assertEqual(data["last_usable_host"], "2001:db8::ffff:ffff:ffff:ffff")

        result = self.run_cli("info", "2001:db8::/64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ブロードキャストアドレス: なし", result.stdout)

    def test_contains(self):
        for ip, code, output in (
            ("192.168.1.255", 0, "含まれる"),
            ("192.168.2.0", 1, "含まれない"),
        ):
            with self.subTest(ip=ip):
                result = self.run_cli("contains", "192.168.1.10/24", ip)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout.strip(), output)

    def test_overlap(self):
        for first, second, code, output in (
            ("192.168.1.0/24", "192.168.1.129/25", 0,
             "重なる: 192.168.1.128/25 (192.168.1.128 ～ 192.168.1.255)"),
            ("2001:db8::/64", "2001:db8::/64", 0,
             "重なる: 2001:db8::/64 (2001:db8:: ～ 2001:db8::ffff:ffff:ffff:ffff)"),
            ("192.168.1.0/24", "192.168.2.0/24", 1, "重ならない"),
        ):
            with self.subTest(first=first, second=second):
                result = self.run_cli("overlap", first, second)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout.strip(), output)

    def test_usage_errors(self):
        for args, reason in (
            (("info", "192.0.2.1"), "無効な CIDR"),
            (("info", "192.0.2.1/33"), "無効な CIDR"),
            (("contains", "192.0.2.0/24", "invalid"), "無効な IP アドレス"),
            (("overlap", "192.0.2.0/24", "invalid/24"), "無効な CIDR"),
            (("contains", "192.0.2.0/24", "::1"), "IP バージョンが異なります"),
            (("overlap", "::/0", "192.0.2.0/24"), "IP バージョンが異なります"),
        ):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn(reason, result.stderr)


if __name__ == "__main__":
    unittest.main()
