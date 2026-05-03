# tools/moltbook_migrate/migrate.py
"""
Migration Engine — Core logic for Moltbook to Beacon Protocol migration.

Orchestrates the complete migration workflow:
1. Fetch Moltbook profile data
2. Generate hardware fingerprint
3. Create Beacon ID anchored to hardware
4. Link to AgentFolio SATP trust profile
5. Publish provenance linkage

Supports both synchronous and asynchronous operation.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .hardware import HardwareFingerprint, HardwareFingerprinter, HardwareFingerprintError
from .moltbook_api import (
    MoltbookAPIError,
    MoltbookClient,
    MoltbookProfile,
    MoltbookProfileNotFoundError,
)

logger = logging.getLogger(__name__)


# API Endpoints
BEACON_DIRECTORY_URL = "https://bottube.ai/api/beacon/directory"
BEACON_ATLAS_URL = "https://50.28.86.131/beacon/atlas"
AGENTFOLIO_SATP_REGISTRY_URL = "https://agentfolio.bot/api/satp"


class MigrationError(Exception):
    """Base exception for migration errors."""

    def __init__(self, message: str, step: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.message = message
        self.step = step
        self.details = details or {}


class MigrationStepError(MigrationError):
    """Raised when a specific migration step fails."""
    pass


class BeaconRegistrationError(MigrationError):
    """Raised when Beacon ID registration fails."""
    pass


class AgentFolioLinkError(MigrationError):
    """Raised when AgentFolio SATP linking fails."""
    pass


@dataclass
class BeaconID:
    """Generated Beacon ID for the migrated agent."""

    agent_id: str  # bcn_ prefix
    public_key_hex: str
    hardware_fingerprint: str
    registration_ts: float
    atlas_registered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "public_key_hex": self.public_key_hex,
            "hardware_fingerprint": self.hardware_fingerprint,
            "registration_ts": self.registration_ts,
            "atlas_registered": self.atlas_registered,
        }


@dataclass
class AgentFolioLink:
    """AgentFolio SATP trust profile link."""

    satp_profile_id: str
    trust_score: float
    verification_level: str
    linked_ts: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "satp_profile_id": self.satp_profile_id,
            "trust_score": self.trust_score,
            "verification_level": self.verification_level,
            "linked_ts": self.linked_ts,
        }


@dataclass
class ProvenanceRecord:
    """Provenance linkage record for the migration."""

    source_platform: str = "moltbook"
    source_handle: str = ""
    beacon_id: str = ""
    satp_profile_id: str = ""
    migration_ts: float = field(default_factory=time.time)
    hardware_fingerprint_hash: str = ""
    original_karma: int = 0
    original_followers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_platform": self.source_platform,
            "source_handle": self.source_handle,
            "beacon_id": self.beacon_id,
            "satp_profile_id": self.satp_profile_id,
            "migration_ts": self.migration_ts,
            "hardware_fingerprint_hash": self.hardware_fingerprint_hash,
            "original_karma": self.original_karma,
            "original_followers": self.original_followers,
        }


@dataclass
class MigrationResult:
    """Complete result of a migration operation."""

    success: bool
    agent_name: str
    beacon_id: Optional[BeaconID] = None
    agentfolio_link: Optional[AgentFolioLink] = None
    provenance: Optional[ProvenanceRecord] = None
    error_message: Optional[str] = None
    steps_completed: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": self.success,
            "agent_name": self.agent_name,
            "steps_completed": self.steps_completed,
            "duration_seconds": self.duration_seconds,
        }

        if self.beacon_id:
            result["beacon_id"] = self.beacon_id.to_dict()

        if self.agentfolio_link:
            result["agentfolio_link"] = self.agentfolio_link.to_dict()

        if self.provenance:
            result["provenance"] = self.provenance.to_dict()

        if self.error_message:
            result["error"] = self.error_message

        return result


class MoltbookMigrator:
    """
    Main migration orchestrator for Moltbook to Beacon Protocol.

    Coordinates profile fetching, hardware fingerprinting, Beacon ID
    generation, and AgentFolio SATP linking.

    Example:
        >>> migrator = MoltbookMigrator()
        >>> result = migrator.migrate("@agent_name")
        >>> if result.success:
        ...     print(f"Beacon ID: {result.beacon_id.agent_id}")
    """

    def __init__(
        self,
        moltbook_client: Optional[MoltbookClient] = None,
        hardware_fingerprinter: Optional[HardwareFingerprinter] = None,
        beacon_directory_url: str = BEACON_DIRECTORY_URL,
        beacon_atlas_url: str = BEACON_ATLAS_URL,
        agentfolio_url: str = AGENTFOLIO_SATP_REGISTRY_URL,
        timeout: int = 30,
    ):
        """
        Initialize the migrator.

        Args:
            moltbook_client: Optional Moltbook API client (lazy-initialized if None)
            hardware_fingerprinter: Optional hardware fingerprinter (lazy-initialized if None)
            beacon_directory_url: Beacon directory API endpoint
            beacon_atlas_url: Beacon Atlas relay endpoint
            agentfolio_url: AgentFolio SATP registry endpoint
            timeout: HTTP request timeout in seconds
        """
        self._moltbook_client = moltbook_client
        self._hardware_fingerprinter_impl = hardware_fingerprinter
        self.beacon_directory_url = beacon_directory_url
        self.beacon_atlas_url = beacon_atlas_url
        self.agentfolio_url = agentfolio_url
        self.timeout = timeout

    @property
    def moltbook_client(self) -> MoltbookClient:
        """Lazy-initialized Moltbook API client."""
        if self._moltbook_client is None:
            self._moltbook_client = MoltbookClient()
        return self._moltbook_client

    @property
    def hardware_fingerprinter(self) -> HardwareFingerprinter:
        """Lazy-initialized hardware fingerprinter."""
        if self._hardware_fingerprinter_impl is None:
            self._hardware_fingerprinter_impl = HardwareFingerprinter()
        return self._hardware_fingerprinter_impl

    def migrate(self, agent_name: str, dry_run: bool = False) -> MigrationResult:
        """
        Execute the complete migration workflow for an agent.

        Args:
            agent_name: The agent's Moltbook handle (with or without @ prefix)
            dry_run: If True, validate steps without making permanent changes

        Returns:
            MigrationResult with success status and migration details
        """
        start_time = time.time()
        result = MigrationResult(success=False, agent_name=agent_name)

        # Normalize agent name
        if not agent_name.startswith("@"):
            agent_name = f"@{agent_name}"
        result.agent_name = agent_name

        try:
            # Step 1: Fetch Moltbook profile
            logger.info(f"Fetching Moltbook profile for {agent_name}")
            profile = self.moltbook_client.get_profile(agent_name)
            result.steps_completed.append("get_agent_profile")

            # Step 2: Validate profile
            self._validate_profile(profile)
            result.steps_completed.append("validate_profile")

            # Step 3: Generate hardware fingerprint
            logger.info("Generating hardware fingerprint")
            fingerprint = self.hardware_fingerprinter.generate()
            result.steps_completed.append("hardware_fingerprint")

            if dry_run:
                logger.info("Dry run complete - no changes made")
                result.success = True
                result.duration_seconds = time.time() - start_time
                return result

            # Step 4: Create Beacon ID
            logger.info("Creating Beacon ID")
            beacon_id = self._create_beacon(profile, fingerprint)
            result.beacon_id = beacon_id
            result.steps_completed.append("create_beacon")

            # Step 5: Link to AgentFolio SATP
            logger.info("Linking to AgentFolio SATP")
            satp_profile_id = self._link_agentfolio(beacon_id, profile)
            result.agentfolio_link = AgentFolioLink(
                satp_profile_id=satp_profile_id,
                trust_score=profile.trust_score if hasattr(profile, 'trust_score') else 0.0,
                verification_level="standard",
                linked_ts=time.time(),
            )
            result.steps_completed.append("link_agentfolio")

            # Step 6: Create provenance record
            logger.info("Creating provenance record")
            result.provenance = ProvenanceRecord(
                source_platform="moltbook",
                source_handle=agent_name,
                beacon_id=beacon_id.agent_id,
                satp_profile_id=satp_profile_id,
                migration_ts=time.time(),
                hardware_fingerprint_hash=hashlib.sha256(
                    fingerprint.fingerprint_hash.encode()
                ).hexdigest()[:16],
                original_karma=profile.karma if hasattr(profile, 'karma') else 0,
                original_followers=profile.follower_count if hasattr(profile, 'followers') else 0,
            )
            result.steps_completed.append("create_provenance")

            result.success = True
            logger.info(f"Migration completed successfully for {agent_name}")

        except MoltbookProfileNotFoundError as e:
            result.error_message = f"Profile not found: {agent_name}"
            logger.error(result.error_message)
        except HardwareFingerprintError as e:
            result.error_message = f"Hardware fingerprinting failed: {e}"
            logger.error(result.error_message)
        except BeaconRegistrationError as e:
            result.error_message = f"Beacon registration failed: {e}"
            logger.error(result.error_message)
        except AgentFolioLinkError as e:
            result.error_message = f"AgentFolio linking failed: {e}"
            logger.error(result.error_message)
        except MigrationStepError as e:
            result.error_message = f"Migration step failed: {e}"
            logger.error(result.error_message)
        except Exception as e:
            result.error_message = f"Unexpected error during migration: {e}"
            logger.exception("Unexpected migration error")

        result.duration_seconds = time.time() - start_time
        return result

    def _validate_profile(self, profile: MoltbookProfile) -> None:
        """
        Validate that a Moltbook profile has all required fields for migration.

        Args:
            profile: The Moltbook profile to validate

        Raises:
            MigrationStepError: If profile validation fails
        """
        if not profile:
            raise MigrationStepError(
                "Profile is empty",
                step="validate_profile",
                details={"profile": profile},
            )

        if hasattr(profile, 'agent_name') and not profile.agent_name:
            raise MigrationStepError(
                "Profile missing agent_name",
                step="validate_profile",
                details={"profile_fields": list(profile.__dict__.keys()) if hasattr(profile, '__dict__') else []},
            )

        logger.debug(f"Profile validation passed for {getattr(profile, 'agent_name', 'unknown')}")

    def _create_beacon(self, profile: MoltbookProfile, fingerprint: HardwareFingerprint) -> BeaconID:
        """
        Create and register a new Beacon ID for the agent.

        Args:
            profile: The Moltbook profile
            fingerprint: The hardware fingerprint

        Returns:
            BeaconID object with registration details

        Raises:
            BeaconRegistrationError: If Beacon registration fails
        """
        # Generate agent ID with bcn_ prefix
        timestamp = int(time.time())
        agent_id_base = f"{profile.agent_name}_{timestamp}" if hasattr(profile, 'agent_name') else f"agent_{timestamp}"
        agent_id_hash = hashlib.sha256(agent_id_base.encode()).hexdigest()[:24]
        agent_id = f"bcn_{agent_id_hash}"

        # Generate a mock public key (in production, this would be from actual key generation)
        public_key_hex = hashlib.sha256(
            f"{agent_id}{fingerprint.fingerprint_hash}".encode()
        ).hexdigest()

        beacon_id = BeaconID(
            agent_id=agent_id,
            public_key_hex=public_key_hex,
            hardware_fingerprint=fingerprint.fingerprint_hash,
            registration_ts=time.time(),
            atlas_registered=False,
        )

        # Try to register with Atlas
        try:
            response = requests.post(
                self.beacon_atlas_url,
                json={
                    "agent_id": beacon_id.agent_id,
                    "public_key_hex": beacon_id.public_key_hex,
                    "hardware_fingerprint": beacon_id.hardware_fingerprint,
                    "registration_ts": beacon_id.registration_ts,
                },
                timeout=self.timeout,
            )
            if response.status_code in (200, 201, 202):
                beacon_id.atlas_registered = True
                logger.info(f"Registered beacon {agent_id} with Atlas")
            else:
                logger.warning(f"Atlas registration returned status {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Atlas registration failed (non-fatal): {e}")

        # Publish to directory
        try:
            response = requests.post(
                self.beacon_directory_url,
                json=beacon_id.to_dict(),
                timeout=self.timeout,
            )
            if response.status_code not in (200, 201, 202):
                logger.warning(f"Directory publish returned status {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Directory publish failed (non-fatal): {e}")

        return beacon_id

    def _link_agentfolio(self, beacon_id: BeaconID, profile: MoltbookProfile) -> str:
        """
        Link the Beacon ID to an AgentFolio SATP trust profile.

        Args:
            beacon_id: The registered Beacon ID
            profile: The Moltbook profile

        Returns:
            The SATP profile ID

        Raises:
            AgentFolioLinkError: If AgentFolio linking fails
        """
        # Calculate trust score based on profile metrics
        trust_score = 0.5  # Base score
        if hasattr(profile, 'follower_count') and profile.follower_count:
            trust_score = min(1.0, trust_score + (profile.follower_count / 5000.0))
        if hasattr(profile, 'following_count') and profile.following_count:
            trust_score = min(1.0, trust_score + (profile.following_count / 10000.0))
        if hasattr(profile, 'karma') and profile.karma:
            trust_score = min(1.0, trust_score + (profile.karma / 10000.0))

        # Generate SATP profile ID
        satp_profile_id = f"satp_{hashlib.sha256(beacon_id.agent_id.encode()).hexdigest()[:20]}"

        payload = {
            "beacon_id": beacon_id.agent_id,
            "satp_profile_id": satp_profile_id,
            "trust_score": trust_score,
            "verification_level": "standard",
            "source_platform": "moltbook",
            "source_handle": getattr(profile, 'agent_name', ''),
            "linked_ts": time.time(),
        }

        try:
            response = requests.post(
                self.agentfolio_url,
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code not in (200, 201, 202):
                raise AgentFolioLinkError(
                    f"AgentFolio returned status {response.status_code}",
                    step="link_agentfolio",
                    details={"response": response.text},
                )
            logger.info(f"Linked beacon {beacon_id.agent_id} to SATP profile {satp_profile_id}")
        except requests.RequestException as e:
            raise AgentFolioLinkError(
                f"Failed to connect to AgentFolio: {e}",
                step="link_agentfolio",
            )

        return satp_profile_id