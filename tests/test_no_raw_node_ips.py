# SPDX-License-Identifier: MIT
"""Guard: shipped code addresses RustChain services by hostname, never raw IP.

Why this matters. The RustChain node certificate is issued for a hostname, so
any ``https://<ip>/...`` URL can only succeed with TLS verification turned
off. A raw-IP default therefore either fails outright (verification on) or
quietly pressures the next contributor to add ``verify=False``. Node IPs also
churn: one former attestation-node address now serves an unrelated web app
that answers 200 on every path, so a liveness check against it lies.

The Atlas default is ``https://rustchain.org/beacon/atlas`` and is
overridable for lab nodes with ``BEACON_ATLAS_URL``.

No ``importlib.reload`` here on purpose: reloading ``mcp_server.beacon_lookup``
re-creates ``BeaconLookupError`` and breaks ``pytest.raises`` in
``test_beacon_lookup.py`` depending on collection order. The env override is
tested through ``resolve_atlas_url(env)`` instead.
"""
import re
from pathlib import Path

import pytest

import mcp_server.beacon_lookup as beacon_lookup
import beacon_skill.moltbook_migrate.migrate as migrate

REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTNAME_DEFAULT = "https://rustchain.org/beacon/atlas"

# Known current or former RustChain node addresses. Extend when a node is
# added; never remove one that was retired -- that is the whole point.
KNOWN_NODE_IPS = (
    "50.28.86.131",   # node 1 (behind rustchain.org)
    "50.28.86.153",   # node 2
    "76.8.228.245",   # node 3 (external, offline)
    "38.76.217.189",  # node 4 -- retired; host now serves an unrelated SPA
)

# Shipped code and CI. Docs/examples are deliberately excluded here: they
# are audited separately and may legitimately quote an IP in prose.
SCAN_DIRS = ("beacon_skill", "mcp_server", "tools", "atlas", ".github")
SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".json", ".js", ".toml", ".cfg", ".ini", ".sh"}

_IP_RE = re.compile("|".join(re.escape(ip) for ip in KNOWN_NODE_IPS))


def _scan_files():
    for d in SCAN_DIRS:
        base = REPO_ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in SCAN_SUFFIXES and "__pycache__" not in p.parts:
                yield p


def test_no_known_node_ip_in_shipped_code():
    hits = []
    for path in _scan_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _IP_RE.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "raw RustChain node IP in shipped code (use the hostname):\n" + "\n".join(hits)


@pytest.mark.parametrize("mod", [beacon_lookup, migrate], ids=["beacon_lookup", "migrate"])
def test_atlas_default_is_hostname(mod):
    assert mod.DEFAULT_BEACON_ATLAS_URL == HOSTNAME_DEFAULT
    assert mod.resolve_atlas_url(env={}) == HOSTNAME_DEFAULT
    assert not _IP_RE.search(mod.resolve_atlas_url(env={}))


@pytest.mark.parametrize("mod", [beacon_lookup, migrate], ids=["beacon_lookup", "migrate"])
def test_atlas_url_env_override(mod):
    assert mod.resolve_atlas_url(env={"BEACON_ATLAS_URL": "  https://lab.example.test/beacon/atlas  "}) == (
        "https://lab.example.test/beacon/atlas"
    )
    # Blank override falls back to the hostname default rather than "".
    assert mod.resolve_atlas_url(env={"BEACON_ATLAS_URL": "   "}) == HOSTNAME_DEFAULT
    assert mod.resolve_atlas_url(env={"BEACON_ATLAS_URL": None}) == HOSTNAME_DEFAULT


def test_module_constant_follows_resolver(monkeypatch):
    monkeypatch.delenv("BEACON_ATLAS_URL", raising=False)
    assert beacon_lookup.BEACON_ATLAS_URL == beacon_lookup.resolve_atlas_url()
    assert migrate.BEACON_ATLAS_URL == migrate.resolve_atlas_url()


def test_migrator_constructor_default_is_hostname():
    m = migrate.MoltbookMigrator()
    assert m.beacon_atlas_url == migrate.BEACON_ATLAS_URL
    assert not _IP_RE.search(m.beacon_atlas_url)


def test_beacon_lookup_does_not_disable_tls_verification():
    """The lookup module must not carry a verify=False anywhere."""
    src = (REPO_ROOT / "mcp_server" / "beacon_lookup.py").read_text(encoding="utf-8")
    assert re.search(r"verify\s*=\s*False", src) is None
