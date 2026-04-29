"""Tests for Moltbook migration importer."""

import json
import hashlib
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Add tools path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'moltbook-migrate'))


class TestHardwareFingerprint(unittest.TestCase):
    """Test hardware fingerprint generation."""

    @patch('platform.system')
    @patch('platform.machine')
    @patch('platform.node')
    @patch('socket.gethostname')
    @patch('subprocess.run')
    def test_fingerprint_structure(self, mock_run, mock_hostname, mock_node, mock_machine, mock_system):
        """Test that fingerprint returns expected structure."""
        mock_system.return_value = 'Linux'
        mock_machine.return_value = 'x86_64'
        mock_node.return_value = 'test-host'
        mock_hostname.return_value = 'test-host'
        mock_run.return_value = MagicMock(
            stdout='1: lo: <LOOPBACK> mtu 65536 qdisc noqueue state DOWN\n2: eth0: <BROADCAST> mtu 1500 qdisc fq_codel state UP qlen 1000    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff'
        )

        from moltbook_migrate import hardware_fingerprint
        fp = hardware_fingerprint()

        self.assertIn('hardware_id', fp)
        self.assertIn('hash', fp)
        self.assertIn('components', fp)
        self.assertTrue(fp['hardware_id'].startswith('hw_'))
        self.assertEqual(len(fp['hash']), 64)  # SHA256 hex


class TestBeaconIDCreation(unittest.TestCase):
    """Test Beacon ID creation."""

    def test_beacon_id_format(self):
        """Test that beacon_id follows bcn_{username}_{hash} format."""
        from moltbook_migrate import create_beacon_id

        fingerprint = {
            'hardware_id': 'hw_test123',
            'hash': 'a' * 64,
            'components': {'platform': 'Linux', 'machine': 'x86_64'},
            'mac_count': 1,
            'disk_count': 1,
        }

        result = create_beacon_id('@testuser', fingerprint)
        beacon_id = result['beacon_id']

        self.assertTrue(beacon_id.startswith('bcn_testuser_'))
        self.assertEqual(len(beacon_id.split('_')), 3)


class TestMoltbookProfileFetch(unittest.TestCase):
    """Test Moltbook profile fetching."""

    @patch('moltbook_migrate.urlopen')
    def test_successful_fetch(self, mock_urlopen):
        """Test successful profile fetch."""
        mock_urlopen.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                read=MagicMock(return_value=json.dumps({
                    'display_name': 'TestUser',
                    'bio': 'Test bio',
                    'karma': 100,
                }).encode())
            )
        )
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        from moltbook_migrate import fetch_moltbook_profile
        result = fetch_moltbook_profile('@testuser')

        self.assertEqual(result['source'], 'moltbook')
        self.assertEqual(result['username'], 'testuser')
        self.assertEqual(result['profile']['display_name'], 'TestUser')


if __name__ == '__main__':
    unittest.main()
