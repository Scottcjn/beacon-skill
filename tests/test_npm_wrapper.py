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


class TestNpmWrapperBootstrap(unittest.TestCase):
    def test_bootstrap_python_selection(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        beacon_js_path = os.path.join(repo_root, "bin", "beacon.js")

        node_script = f"""
        const {{ getBootstrapPython }} = require('{beacon_js_path}');
        const assert = require('assert');

        // Test custom PYTHON env override
        const custom = getBootstrapPython('linux', {{ PYTHON: '/custom/bin/python' }});
        assert.strictEqual(custom.cmd, '/custom/bin/python');

        // Test win32 fallback selection
        const win = getBootstrapPython('win32', {{}});
        assert(win.cmd === 'py' || win.cmd === 'python');

        // Test default unix
        const unix = getBootstrapPython('darwin', {{}});
        assert(unix.cmd === 'python3' || unix.cmd === 'python');

        console.log('OK');
        """
        res = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True, text=True, check=True
        )
        self.assertEqual(res.stdout.strip(), "OK")


if __name__ == "__main__":
    unittest.main()
