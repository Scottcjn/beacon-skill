import json
import os
import subprocess
import unittest


class TestNpmWrapper(unittest.TestCase):
    def test_version_matches_package_json(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pkg_json_path = os.path.join(repo_root, "package.json")
        beacon_js_path = os.path.join(repo_root, "bin", "beacon.js")

        with open(pkg_json_path, "r", encoding="utf-8") as f:
            pkg_data = json.load(f)
        expected_version = pkg_data.get("version")

        res = subprocess.run(
            ["node", beacon_js_path, "--version"],
            capture_output=True, text=True, check=True
        )
        self.assertEqual(res.stdout.strip(), expected_version)


class TestNpmWrapperDeps(unittest.TestCase):
    def test_deps_fingerprint(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        beacon_js_path = os.path.join(repo_root, "bin", "beacon.js")

        node_script = f"""
        const {{ getDepsFingerprint, REQUIRED_DEPS }} = require('{beacon_js_path}');
        const assert = require('assert');

        const fp = getDepsFingerprint();
        assert(typeof fp === 'string');
        assert(fp.includes('requests>=2.25'));
        assert(fp.includes('cryptography>=41'));
        console.log('OK');
        """
        res = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True, text=True, check=True
        )
        self.assertEqual(res.stdout.strip(), "OK")


if __name__ == "__main__":
    unittest.main()
