# tools/moltbook_migrate/hardware.py
"""
Hardware Fingerprinting — Machine identification for Beacon Protocol.

Generates a unique hardware fingerprint from the operator's machine using:
- MAC address (primary network interface)
- CPU information (model, cores, flags)
- Hostname
- Machine ID (if available on Linux/macOS)

This fingerprint is used to anchor a Beacon ID to the physical machine,
providing substrate-level identity verification.

Compatible with: Linux, macOS, Windows (limited)
"""

import hashlib
import logging
import platform
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HardwareFingerprintError(Exception):
    """Raised when hardware fingerprinting fails."""
    pass


@dataclass
class HardwareFingerprint:
    """
    Hardware fingerprint for machine identification.

    Contains multiple signals for robust machine identification:
    - mac_address: Primary network interface MAC (normalized)
    - cpu_info: CPU model and core count
    - hostname: Machine hostname
    - machine_id: OS-level machine identifier (when available)
    - platform: Operating system name
    - fingerprint_hash: SHA-256 hash of combined signals

    The fingerprint_hash is the canonical identifier used for
    Beacon ID anchoring.
    """

    mac_address: str
    cpu_info: str
    hostname: str
    machine_id: Optional[str]
    platform: str
    platform_version: str
    fingerprint_hash: str
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_anchor_payload(self) -> Dict[str, Any]:
        """Convert to payload for Beacon ID anchoring."""
        return {
            "fingerprint_hash": self.fingerprint_hash,
            "mac_address": self.mac_address,
            "hostname": self.hostname,
            "platform": f"{self.platform} {self.platform_version}",
            "cpu_info": self.cpu_info,
            "machine_id": self.machine_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HardwareFingerprint":
        """Create from dictionary (for serialization)."""
        return cls(
            mac_address=data["mac_address"],
            cpu_info=data["cpu_info"],
            hostname=data["hostname"],
            machine_id=data.get("machine_id"),
            platform=data["platform"],
            platform_version=data["platform_version"],
            fingerprint_hash=data["fingerprint_hash"],
            raw_data=data.get("raw_data", {}),
        )


class HardwareFingerprinter:
    """
    Generate hardware fingerprints for machine identification.

    Collects multiple hardware signals and combines them into a
    deterministic fingerprint hash. Designed to be robust across
    reboots and network changes while remaining unique per machine.

    Example:
        >>> fp = HardwareFingerprinter()
        >>> fingerprint = fp.generate()
        >>> print(f"Fingerprint: {fingerprint.fingerprint_hash}")
    """

    # Network interfaces to prefer (in order)
    PREFERRED_INTERFACES = ["en0", "eth0", "wlan0", "en1", "wl0", "wifi0"]

    def __init__(self, strict: bool = True):
        """
        Initialize the hardware fingerprinter.

        Args:
            strict: If True, raise exception on missing signals.
                   If False, use available signals only.
        """
        self.strict = strict

    def generate(self) -> HardwareFingerprint:
        """
        Generate a complete hardware fingerprint for this machine.

        Returns:
            HardwareFingerprint with all available signals

        Raises:
            HardwareFingerprintError: If strict=True and required
                                      signals are unavailable
        """
        raw_data: Dict[str, Any] = {}

        # Collect all signals
        mac_address = self._get_mac_address()
        cpu_info = self._get_cpu_info()
        hostname = self._get_hostname()
        machine_id = self._get_machine_id()

        raw_data["mac_address"] = mac_address
        raw_data["cpu_info"] = cpu_info
        raw_data["hostname"] = hostname
        raw_data["machine_id"] = machine_id

        # Generate fingerprint hash
        fingerprint_hash = self._compute_fingerprint_hash(
            mac_address, cpu_info, hostname, machine_id
        )

        return HardwareFingerprint(
            mac_address=mac_address,
            cpu_info=cpu_info,
            hostname=hostname,
            machine_id=machine_id,
            platform=platform.system(),
            platform_version=platform.release(),
            fingerprint_hash=fingerprint_hash,
            raw_data=raw_data,
        )

    def _get_mac_address(self) -> str:
        """
        Get the primary MAC address from network interfaces.

        Returns normalized MAC address or raises if strict mode enabled.
        """
        mac_addresses = self._scan_mac_addresses()

        if not mac_addresses:
            if self.strict:
                raise HardwareFingerprintError("No MAC address found")
            logger.warning("No MAC address found, using fallback")
            return "00:00:00:00:00:00"

        # Prefer known-good interfaces
        for preferred in self.PREFERRED_INTERFACES:
            if preferred in mac_addresses:
                return self._normalize_mac(mac_addresses[preferred])

        # Fall back to first available
        first_mac = next(iter(mac_addresses.values()))
        return self._normalize_mac(first_mac)

    def _scan_mac_addresses(self) -> Dict[str, str]:
        """Scan all network interfaces for MAC addresses."""
        mac_addresses: Dict[str, str] = {}

        system = platform.system()

        if system == "Linux":
            mac_addresses = self._scan_linux_interfaces()
        elif system == "Darwin":  # macOS
            mac_addresses = self._scan_macos_interfaces()
        elif system == "Windows":
            mac_addresses = self._scan_windows_interfaces()

        # Filter out loopback and empty addresses
        mac_addresses = {
            iface: mac for iface, mac in mac_addresses.items()
            if mac and mac.upper() not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF")
        }

        return mac_addresses

    def _scan_linux_interfaces(self) -> Dict[str, str]:
        """Scan Linux network interfaces via /sys/class/net/."""
        interfaces: Dict[str, str] = {}

        import os
        net_path = "/sys/class/net"

        if not os.path.exists(net_path):
            return interfaces

        try:
            for iface in os.listdir(net_path):
                mac_path = os.path.join(net_path, iface, "address")
                if os.path.exists(mac_path):
                    try:
                        with open(mac_path, "r") as f:
                            interfaces[iface] = f.read().strip()
                    except IOError:
                        pass
        except OSError as e:
            logger.warning(f"Failed to scan Linux interfaces: {e}")

        return interfaces

    def _scan_macos_interfaces(self) -> Dict[str, str]:
        """Scan macOS network interfaces via system_profiler."""
        interfaces: Dict[str, str] = {}

        try:
            result = subprocess.run(
                ["system_profiler", "SPNetworkDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)

                for _, iface_data in data.items():
                    if isinstance(iface_data, list):
                        for iface in iface_data:
                            if "MAC Address" in iface:
                                interfaces[iface.get("interface", "unknown")] = \
                                    iface["MAC Address"]
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to scan macOS interfaces: {e}")

        # Fallback: use ifconfig
        if not interfaces:
            try:
                result = subprocess.run(
                    ["ifconfig"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    current_iface = None
                    for line in result.stdout.split("\n"):
                        if ":" in line and not line.startswith(" "):
                            current_iface = line.split(":")[0]
                        elif "ether" in line.lower() and current_iface:
                            parts = line.split()
                            if len(parts) >= 2:
                                interfaces[current_iface] = parts[1]
            except OSError:
                pass

        return interfaces

    def _scan_windows_interfaces(self) -> Dict[str, str]:
        """Scan Windows network interfaces via getmac or ipconfig."""
        interfaces: Dict[str, str] = {}

        try:
            # Try getmac first
            result = subprocess.run(
                ["getmac", "/v", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                timeout=30,
                shell=True,
            )

            if result.returncode == 0:
                import csv
                import io

                reader = csv.reader(io.StringIO(result.stdout))
                for row in reader:
                    if len(row) >= 3:
                        # Connection Name, Transport Name, MAC Address
                        iface_name = row[0].strip('"')
                        mac_addr = row[2].strip('"')
                        if mac_addr and "-" in mac_addr:
                            interfaces[iface_name] = mac_addr.replace("-", ":")
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Failed to scan Windows interfaces with getmac: {e}")

        return interfaces

    def _normalize_mac(self, mac: str) -> str:
        """Normalize MAC address to standard format (XX:XX:XX:XX:XX:XX)."""
        # Remove common separators and convert to uppercase
        normalized = mac.replace("-", ":").replace(".", ":").upper()

        # Ensure proper format
        parts = normalized.split(":")
        if len(parts) != 6:
            # Try to pad or truncate
            normalized = ":".join(p.zfill(2) for p in parts[:6])

        return normalized

    def _get_cpu_info(self) -> str:
        """
        Get CPU model and core information.

        Returns a string combining CPU model, core count, and flags.
        """
        cpu_data: Dict[str, Any] = {
            "model": platform.processor() or "unknown",
            "cores": self._get_core_count(),
        }

        # Add additional CPU flags/features on Linux
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    cpuinfo = f.read()

                # Get CPU model
                for line in cpuinfo.split("\n"):
                    if line.startswith("model name"):
                        cpu_data["model"] = line.split(":")[1].strip()
                        break

                # Get key flags
                flags = []
                for line in cpuinfo.split("\n"):
                    if line.startswith("flags"):
                        raw_flags = line.split(":")[1].strip().split()
                        # Select important flags
                        important = ["lm", "sse", "sse2", "sse4_1", "sse4_2", "avx", "avx2"]
                        flags = [f for f in important if f in raw_flags]
                        break

                if flags:
                    cpu_data["flags"] = sorted(flags)
            except IOError:
                pass

        # Add CPU frequency if available
        freq = self._get_cpu_frequency()
        if freq:
            cpu_data["frequency_mhz"] = freq

        # Serialize to deterministic string
        parts = [
            cpu_data.get("model", "unknown"),
            str(cpu_data.get("cores", 0)),
        ]

        if "flags" in cpu_data:
            parts.append(",".join(cpu_data["flags"]))

        return "|".join(parts)

    def _get_core_count(self) -> int:
        """Get the number of CPU cores."""
        try:
            return os.cpu_count() or 1
        except Exception:
            return 1

    def _get_cpu_frequency(self) -> Optional[float]:
        """Get CPU frequency in MHz (if available)."""
        if platform.system() == "Linux":
            try:
                with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "r") as f:
                    return int(f.read().strip()) / 1000  # Convert kHz to MHz
            except IOError:
                pass
        return None

    def _get_hostname(self) -> str:
        """Get the machine hostname."""
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _get_machine_id(self) -> Optional[str]:
        """
        Get OS-level machine ID (if available).

        Linux: /etc/machine-id
        macOS: IOKit registry
        Windows: registry or WMI
        """
        system = platform.system()

        if system == "Linux":
            return self._read_linux_machine_id()
        elif system == "Darwin":
            return self._read_macos_machine_id()
        elif system == "Windows":
            return self._read_windows_machine_id()

        return None

    def _read_linux_machine_id(self) -> Optional[str]:
        """Read Linux machine-id from /etc/machine-id or /var/lib/dbus/machine-id."""
        import os

        paths = ["/etc/machine-id", "/var/lib/dbus/machine-id"]

        for path in paths:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        machine_id = f.read().strip()
                        if machine_id:
                            return machine_id
                except IOError:
                    pass

        return None

    def _read_macos_machine_id(self) -> Optional[str]:
        """Read macOS hardware UUID via IOKit."""
        try:
            # Use system_profiler to get hardware UUID
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                hardware = data.get("SPHardwareDataType", [])

                if hardware and "hardware" in hardware[0]:
                    return hardware[0]["hardware"].get("uuid")

        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass

        # Fallback: use IOPlatformUUID
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "IOPlatformUUID" in line:
                        parts = line.split('"')
                        if len(parts) >= 4:
                            return parts[3]
        except OSError:
            pass

        return None

    def _read_windows_machine_id(self) -> Optional[str]:
        """Read Windows machine ID from registry or WMI."""
        try:
            import winreg

            # Try registry first
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            )

            try:
                machine_id, _ = winreg.QueryValueEx(key, "MachineGuid")
                return machine_id
            finally:
                winreg.CloseKey(key)
        except ImportError:
            # Fallback: use wmic
            try:
                result = subprocess.run(
                    ["wmic", "csproduct", "get", "UUID"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if len(lines) >= 2:
                        uuid_str = lines[1].strip()
                        if uuid_str and uuid_str != "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                            return uuid_str
            except OSError:
                pass

        return None

    def _compute_fingerprint_hash(
        self,
        mac_address: str,
        cpu_info: str,
        hostname: str,
        machine_id: Optional[str],
    ) -> str:
        """
        Compute SHA-256 fingerprint hash from hardware signals.

        Combines multiple signals to create a unique, deterministic
        fingerprint for this machine.
        """
        # Normalize inputs
        mac_normalized = mac_address.upper().replace(":", "")
        cpu_normalized = cpu_info.replace(" ", "").replace("\n", "")
        hostname_normalized = hostname.lower().strip()

        # Build deterministic string
        components = [
            f"mac:{mac_normalized}",
            f"cpu:{cpu_normalized}",
            f"host:{hostname_normalized}",
        ]

        if machine_id:
            components.append(f"mid:{machine_id}")

        # Add platform info for cross-platform uniqueness
        components.append(f"os:{platform.system()}:{platform.release()}")

        # Combine and hash
        combined = "|".join(sorted(components))
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# Convenience function for quick fingerprinting
def generate_fingerprint(strict: bool = True) -> HardwareFingerprint:
    """
    Generate a hardware fingerprint for the current machine.

    Args:
        strict: If True, raise exception on missing signals.

    Returns:
        HardwareFingerprint with machine identification data.
    """
    fingerprinter = HardwareFingerprinter(strict=strict)
    return fingerprinter.generate()


# Module-level import for os.cpu_count
import os  # noqa: E402