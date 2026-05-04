# tools/moltbook_migrate/tests/test_migrate.py
"""
Tests for migrate.py — Moltbook to Beacon Protocol migration engine.
"""

import hashlib
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tools.moltbook_migrate.hardware import (
    HardwareFingerprint,
    HardwareFingerprinter,
    HardwareFingerprintError,
)
from tools.moltbook_migrate.migrate import (
    AgentFolioLink,
    AgentFolioLinkError,
    BeaconID,
    BeaconRegistrationError,
    MigrationError,
    MigrationResult,
    MigrationStepError,
    MoltbookMigrator,
    ProvenanceRecord,
)
from tools.moltbook_migrate.moltbook_api import (
    MoltbookAPIError,
    MoltbookClient,
    MoltbookKarmaHistory,
    MoltbookProfile,
    MoltbookProfileNotFoundError,
    MoltbookRateLimitError,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_moltbook_profile():
    """Create a mock MoltbookProfile with typical data."""
    karma_history = [
        MoltbookKarmaHistory(period="7d", score=150, change=25),
        MoltbookKarmaHistory(period="30d", score=600, change=100),
        MoltbookKarmaHistory(period="all_time", score=2500, change=0),
    ]
    return MoltbookProfile(
        agent_name="@testagent",
        display_name="Test Agent",
        bio="A test agent for migration",
        avatar_url="https://moltbook.social/avatars/testagent.jpg",
        follower_count=1500,
        following_count=200,
        karma=2500,
        karma_history=karma_history,
        created_at="2023-01-15T10:30:00Z",
        verified=True,
        content_count=150,
        video_count=25,
        badges=["Early Adopter", "Verified Creator"],
        interests=["AI", "Technology", "Open Source"],
        location="San Francisco, CA",
        website="https://testagent.example",
        raw_data={"api_version": "v1"},
    )


@pytest.fixture
def mock_hardware_fingerprint():
    """Create a mock HardwareFingerprint."""
    return HardwareFingerprint(
        mac_address="00:11:22:33:44:55",
        cpu_info="Intel i9-12900K (16 cores)",
        hostname="test-machine",
        machine_id="a1b2c3d4e5f6",
        platform="Linux",
        platform_version="6.1.0",
        fingerprint_hash="abc123def456789",
        raw_data={"raw_signal": "data"},
    )


@pytest.fixture
def mock_beacon_id():
    """Create a mock BeaconID."""
    return BeaconID(
        agent_id="bcn_test123abc",
        public_key_hex="deadbeef123456",
        hardware_fingerprint="abc123def456789",
        registration_ts=time.time(),
        atlas_registered=True,
    )


@pytest.fixture
def mock_agentfolio_link():
    """Create a mock AgentFolioLink."""
    return AgentFolioLink(
        satp_profile_id="satp_xyz789",
        trust_score=0.92,
        verification_level="hardware_verified",
        linked_ts=time.time(),
    )


@pytest.fixture
def mock_moltbook_client(mock_moltbook_profile):
    """Create a mock MoltbookClient."""
    client = MagicMock()
    client.fetch_profile.return_value = mock_moltbook_profile
    return client


@pytest.fixture
def mock_hardware_fingerprinter(mock_hardware_fingerprint):
    """Create a mock HardwareFingerprinter."""
    fingerprinter = MagicMock()
    fingerprinter.generate.return_value = mock_hardware_fingerprint
    return fingerprinter


# =============================================================================
# Test MigrationResult Dataclass
# =============================================================================

class TestMigrationResult:
    """Tests for MigrationResult dataclass."""

    def test_to_dict_success(self, mock_beacon_id, mock_agentfolio_link, mock_moltbook_profile):
        """Test to_dict with successful migration."""
        karma_history = [
            MoltbookKarmaHistory(period="7d", score=150, change=25),
        ]
        profile = MoltbookProfile(
            agent_name="@testagent",
            display_name="Test Agent",
            bio="Test bio",
            avatar_url="https://example.com/avatar.jpg",
            follower_count=100,
            following_count=50,
            karma=200,
            karma_history=karma_history,
        )
        provenance = ProvenanceRecord(
            source_platform="moltbook",
            source_handle="@testagent",
            beacon_id="bcn_test123",
            satp_profile_id="satp_abc",
            hardware_fingerprint_hash="fp_hash_123",
            original_karma=200,
            original_followers=100,
        )

        result = MigrationResult(
            success=True,
            agent_name="@testagent",
            beacon_id=mock_beacon_id,
            agentfolio_link=mock_agentfolio_link,
            provenance=provenance,
            steps_completed=["fetch_profile", "generate_fingerprint", "register_beacon"],
            duration_seconds=2.5,
        )

        result_dict = result.to_dict()

        assert result_dict["success"] is True
        assert result_dict["agent_name"] == "@testagent"
        assert result_dict["beacon_id"]["agent_id"] == "bcn_test123abc"
        assert result_dict["beacon_id"]["public_key_hex"] == "deadbeef123456"
        assert result_dict["agentfolio_link"]["satp_profile_id"] == "satp_xyz789"
        assert result_dict["provenance"]["source_platform"] == "moltbook"
        assert result_dict["steps_completed"] == [
            "fetch_profile", "generate_fingerprint", "register_beacon"
        ]
        assert result_dict["duration_seconds"] == 2.5
        assert "error" not in result_dict

    def test_to_dict_failure(self):
        """Test to_dict with failed migration."""
        result = MigrationResult(
            success=False,
            agent_name="@testagent",
            error_message="Profile not found",
            steps_completed=["fetch_profile"],
            duration_seconds=0.5,
        )

        result_dict = result.to_dict()

        assert result_dict["success"] is False
        assert result_dict["agent_name"] == "@testagent"
        assert result_dict["error"] == "Profile not found"
        assert "beacon_id" not in result_dict
        assert "agentfolio_link" not in result_dict
        assert "provenance" not in result_dict

    def test_to_dict_partial_success(self, mock_beacon_id):
        """Test to_dict with partial success (no AgentFolio link)."""
        result = MigrationResult(
            success=False,
            agent_name="@testagent",
            beacon_id=mock_beacon_id,
            error_message="AgentFolio link failed",
            steps_completed=["fetch_profile", "generate_fingerprint", "register_beacon"],
            duration_seconds=3.0,
        )

        result_dict = result.to_dict()

        assert result_dict["success"] is False
        assert "beacon_id" in result_dict
        assert "agentfolio_link" not in result_dict
        assert "error" in result_dict


# =============================================================================
# Test MoltbookProfile to_migration_payload
# =============================================================================

class TestMoltbookProfileMigrationPayload:
    """Tests for MoltbookProfile.to_migration_payload method."""

    def test_to_migration_payload(self, mock_moltbook_profile):
        """Test conversion to migration payload."""
        payload = mock_moltbook_profile.to_migration_payload()

        assert payload["source_platform"] == "moltbook"
        assert payload["source_handle"] == "@testagent"
        assert payload["display_name"] == "Test Agent"
        assert payload["bio"] == "A test agent for migration"
        assert payload["avatar_url"] == "https://moltbook.social/avatars/testagent.jpg"
        assert payload["follower_count"] == 1500
        assert payload["karma"] == 2500
        assert payload["verified"] is True
        assert payload["content_count"] == 150
        assert payload["interests"] == ["AI", "Technology", "Open Source"]
        assert payload["location"] == "San Francisco, CA"
        assert "migration_timestamp" in payload

    def test_to_migration_payload_minimal_profile(self):
        """Test payload with minimal profile data."""
        profile = MoltbookProfile(
            agent_name="@minimal",
            display_name="Minimal",
            bio="",
            avatar_url="",
            follower_count=0,
            following_count=0,
            karma=0,
        )

        payload = profile.to_migration_payload()

        assert payload["source_platform"] == "moltbook"
        assert payload["source_handle"] == "@minimal"
        assert payload["display_name"] == "Minimal"
        assert payload["bio"] == ""
        assert payload["follower_count"] == 0
        assert payload["verified"] is False


# =============================================================================
# Test HardwareFingerprint
# =============================================================================

class TestHardwareFingerprint:
    """Tests for HardwareFingerprint dataclass."""

    def test_to_anchor_payload(self, mock_hardware_fingerprint):
        """Test conversion to anchor payload."""
        payload = mock_hardware_fingerprint.to_anchor_payload()

        assert payload["fingerprint_hash"] == "abc123def456789"
        assert payload["mac_address"] == "00:11:22:33:44:55"
        assert payload["hostname"] == "test-machine"
        assert payload["platform"] == "Linux 6.1.0"
        assert payload["cpu_info"] == "Intel i9-12900K (16 cores)"
        assert payload["machine_id"] == "a1b2c3d4e5f6"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "mac_address": "aa:bb:cc:dd:ee:ff",
            "cpu_info": "AMD Ryzen 9 5950X (16 cores)",
            "hostname": "ryzen-machine",
            "machine_id": "xyz789",
            "platform": "Linux",
            "platform_version": "5.15.0",
            "fingerprint_hash": "hash123",
            "raw_data": {"extra": "data"},
        }

        fp = HardwareFingerprint.from_dict(data)

        assert fp.mac_address == "aa:bb:cc:dd:ee:ff"
        assert fp.cpu_info == "AMD Ryzen 9 5950X (16 cores)"
        assert fp.hostname == "ryzen-machine"
        assert fp.machine_id == "xyz789"
        assert fp.platform == "Linux"
        assert fp.platform_version == "5.15.0"
        assert fp.fingerprint_hash == "hash123"
        assert fp.raw_data == {"extra": "data"}

    def test_from_dict_optional_fields(self):
        """Test from_dict with missing optional fields."""
        data = {
            "mac_address": "00:00:00:00:00:00",
            "cpu_info": "ARM Processor",
            "hostname": "arm-device",
            "platform": "Linux",
            "platform_version": "6.0.0",
            "fingerprint_hash": "arm_hash",
        }

        fp = HardwareFingerprint.from_dict(data)

        assert fp.machine_id is None
        assert fp.raw_data == {}


# =============================================================================
# Test MoltbookMigrator - Successful Migration
# =============================================================================

class TestMoltbookMigratorSuccess:
    """Tests for successful migration scenarios."""

    @patch("tools.moltbook_migrate.migrate.HardwareFingerprinter")
    def test_migrate_success(
        self,
        mock_fingerprinter_cls,
        mock_moltbook_profile,
        mock_hardware_fingerprint,
        mock_beacon_id,
        mock_agentfolio_link,
    ):
        """Test successful full migration."""
        # Setup mocks
        mock_fingerprinter = MagicMock()
        mock_fingerprinter.generate.return_value = mock_hardware_fingerprint
        mock_fingerprinter_cls.return_value = mock_fingerprinter

        # Mock HTTP calls for beacon registration and AgentFolio linking
        with patch("tools.moltbook_migrate.migrate.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "agent_id": "bcn_test123abc",
                "public_key_hex": "deadbeef123456",
                "hardware_fingerprint": "abc123def456789",
                "registration_ts": time.time(),
                "atlas_registered": True,
            }
            mock_post.return_value = mock_response

            # Execute migration
            migrator = MoltbookMigrator(
                moltbook_client=MagicMock(get_profile=MagicMock(return_value=mock_moltbook_profile)),
                hardware_fingerprinter=mock_fingerprinter
            )
            result = migrator.migrate("@testagent")

        # Verify result
        assert result.success is True
        assert result.agent_name == "@testagent"
        assert result.beacon_id is not None
        assert result.agentfolio_link is not None
        assert result.provenance is not None
        assert len(result.steps_completed) > 0
        assert result.error_message is None

    @patch("tools.moltbook_migrate.migrate.MoltbookClient")
    @patch("tools.moltbook_migrate.migrate.HardwareFingerprinter")
    def test_migrate_dry_run(
        self,
        mock_fingerprinter_cls,
        mock_client_cls,
        mock_moltbook_profile,
        mock_hardware_fingerprint,
    ):
        """Test dry-run migration (no actual registration)."""
        mock_fingerprinter = MagicMock()
        mock_fingerprinter.generate.return_value = mock_hardware_fingerprint
        mock_fingerprinter_cls.return_value = mock_fingerprinter

        migrator = MoltbookMigrator(
                moltbook_client=MagicMock(get_profile=MagicMock(return_value=mock_moltbook_profile)),
                hardware_fingerprinter=mock_fingerprinter
            )
        result = migrator.migrate("@testagent", dry_run=True)

        assert result.success is True
        assert result.steps_completed == ["get_agent_profile", "validate_profile", "hardware_fingerprint"]
        assert result.beacon_id is None
        assert result.agentfolio_link is None


# =============================================================================
# Test MoltbookMigrator - Error Scenarios
# =============================================================================

class TestMoltbookMigratorErrors:
    """Tests for migration error scenarios."""

    def test_migrate_profile_not_found(self, mock_moltbook_profile, mock_hardware_fingerprint):
        """Test migration when profile is not found."""
        mock_client = MagicMock()
        mock_client.get_profile.side_effect = MoltbookProfileNotFoundError(
            "Profile not found", status_code=404
        )
        
        migrator = MoltbookMigrator(
            moltbook_client=mock_client,
            hardware_fingerprinter=MagicMock(generate=MagicMock(return_value=mock_hardware_fingerprint))
        )
        result = migrator.migrate("@nonexistent")

        assert result.success is False
        assert "not found" in result.error_message.lower()

    def test_migrate_rate_limited(self, mock_moltbook_profile, mock_hardware_fingerprint):
        """Test migration when rate limited by Moltbook API."""
        mock_client = MagicMock()
        mock_client.get_profile.side_effect = MoltbookRateLimitError(
            "Rate limit exceeded", status_code=429
        )
        
        migrator = MoltbookMigrator(
            moltbook_client=mock_client,
            hardware_fingerprinter=MagicMock(generate=MagicMock(return_value=mock_hardware_fingerprint))
        )
        result = migrator.migrate("@testagent")

        assert result.success is False
        assert result.error_message is not None

    @patch("tools.moltbook_migrate.migrate.MoltbookClient")
    @patch("tools.moltbook_migrate.migrate.HardwareFingerprinter")
    def test_migrate_api_error(self, mock_fingerprinter_cls, mock_client_cls):
        """Test migration when Moltbook API returns error."""
        mock_client = MagicMock()
        mock_client.get_profile.side_effect = MoltbookAPIError(
            "Internal server error",
            status_code=500,
        )
        mock_client_cls.return_value = mock_client

        migrator = MoltbookMigrator(
            moltbook_client=mock_client,
            hardware_fingerprinter=MagicMock()
        )
        result = migrator.migrate("@testagent")

        assert result.success is False
        assert result.error_message is not None

    @patch("tools.moltbook_migrate.migrate.MoltbookClient")
    @patch("tools.moltbook_migrate.migrate.HardwareFingerprinter")
    def test_migrate_hardware_fingerprint_error(
        self, mock_fingerprinter_cls, mock_client_cls, mock_moltbook_profile
    ):
        """Test migration when hardware fingerprinting fails."""
        mock_fingerprinter = MagicMock()
        mock_fingerprinter.generate.side_effect = HardwareFingerprintError(
            "Cannot read MAC address"
        )
        mock_fingerprinter_cls.return_value = mock_fingerprinter

        migrator = MoltbookMigrator(
                moltbook_client=MagicMock(get_profile=MagicMock(return_value=mock_moltbook_profile)),
                hardware_fingerprinter=mock_fingerprinter
            )
        result = migrator.migrate("@testagent")

        assert result.success is False
        assert result.error_message is not None

    @patch("tools.moltbook_migrate.migrate.MoltbookClient")
    @patch("tools.moltbook_migrate.migrate.HardwareFingerprinter")
    def test_migrate_beacon_registration_error(
        self,
        mock_fingerprinter_cls,
        mock_client_cls,
        mock_moltbook_profile,
        mock_hardware_fingerprint,
    ):
        """Test migration when Beacon registration fails."""
        mock_fingerprinter = MagicMock()
        mock_fingerprinter.generate.return_value = mock_hardware_fingerprint
        mock_fingerprinter_cls.return_value = mock_fingerprinter

        with patch("tools.moltbook_migrate.migrate.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_post.return_value = mock_response

            migrator = MoltbookMigrator(
                moltbook_client=MagicMock(get_profile=MagicMock(return_value=mock_moltbook_profile)),
                hardware_fingerprinter=mock_fingerprinter
            )
            result = migrator.migrate("@testagent")

        assert result.success is False
        assert result.error_message is not None


# =============================================================================
# Test BeaconID
# =============================================================================

class TestBeaconID:
    """Tests for BeaconID dataclass."""

    def test_to_dict(self, mock_beacon_id):
        """Test BeaconID to_dict conversion."""
        result = mock_beacon_id.to_dict()

        assert result["agent_id"] == "bcn_test123abc"
        assert result["public_key_hex"] == "deadbeef123456"
        assert result["hardware_fingerprint"] == "abc123def456789"
        assert result["atlas_registered"] is True
        assert "registration_ts" in result


# =============================================================================
# Test AgentFolioLink
# =============================================================================

class TestAgentFolioLink:
    """Tests for AgentFolioLink dataclass."""

    def test_to_dict(self, mock_agentfolio_link):
        """Test AgentFolioLink to_dict conversion."""
        result = mock_agentfolio_link.to_dict()

        assert result["satp_profile_id"] == "satp_xyz789"
        assert result["trust_score"] == 0.92
        assert result["verification_level"] == "hardware_verified"
        assert "linked_ts" in result


# =============================================================================
# Test ProvenanceRecord
# =============================================================================

class TestProvenanceRecord:
    """Tests for ProvenanceRecord dataclass."""

    def test_to_dict(self):
        """Test ProvenanceRecord to_dict conversion."""
        provenance = ProvenanceRecord(
            source_platform="moltbook",
            source_handle="@testagent",
            beacon_id="bcn_abc123",
            satp_profile_id="satp_xyz",
            hardware_fingerprint_hash="fp_hash_456",
            original_karma=500,
            original_followers=100,
        )

        result = provenance.to_dict()

        assert result["source_platform"] == "moltbook"
        assert result["source_handle"] == "@testagent"
        assert result["beacon_id"] == "bcn_abc123"
        assert result["satp_profile_id"] == "satp_xyz"
        assert result["hardware_fingerprint_hash"] == "fp_hash_456"
        assert result["original_karma"] == 500
        assert result["original_followers"] == 100
        assert "migration_ts" in result


# =============================================================================
# Test Exception Classes
# =============================================================================

class TestMigrationExceptions:
    """Tests for migration exception classes."""

    def test_migration_error(self):
        """Test MigrationError attributes."""
        error = MigrationError(
            "Something went wrong",
            step="fetch_profile",
            details={"code": "ERR_FETCH"},
        )

        assert error.message == "Something went wrong"
        assert error.step == "fetch_profile"
        assert error.details == {"code": "ERR_FETCH"}

    def test_migration_step_error(self):
        """Test MigrationStepError."""
        error = MigrationStepError(
            "Step failed",
            step="register_beacon",
        )

        assert error.message == "Step failed"
        assert error.step == "register_beacon"

    def test_beacon_registration_error(self):
        """Test BeaconRegistrationError."""
        error = BeaconRegistrationError(
            "Registration failed",
            step="register_beacon",
            details={"status_code": 500},
        )

        assert error.message == "Registration failed"
        assert error.step == "register_beacon"
        assert error.details["status_code"] == 500

    def test_agentfolio_link_error(self):
        """Test AgentFolioLinkError."""
        error = AgentFolioLinkError(
            "Linking failed",
            step="link_agentfolio",
        )

        assert error.message == "Linking failed"
        assert error.step == "link_agentfolio"