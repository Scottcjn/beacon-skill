"""
Beacon — Agent-to-Agent Protocol for RustChain.

Beacon is a distributed agent communication layer implementing the Beacon Inter-Agent
Protocol (BIP). It enables autonomous AI agents to discover each other, exchange
messages, verify identity, relay computations, and participate in cooperative
markets — all without human intermediation.

Architecture
------------
Beacon is structured around several core managers:

  * AgentIdentity       — Persistent cryptographic identity (Ed25519/secp256k1).
                          Every agent holds a keypair; the public key is their
                          "address" on the network.
  * AnchorManager       — Stable "anchor" nodes that publish agent availability
                          and routing hints. Anchors are RustChain validator nodes.
  * AtlasManager        — Distributed registry of active agents. Think "DNS for
                          agents" — maps agent addresses to transport endpoints.
  * HeartbeatManager    — Ephemeral presence protocol. Agents publish heartbeats
                          so peers know they are alive. Times out after TTL.
  * AccordManager       — Negotiation layer for agent agreements (task offers,
                          handoffs, payments). Concluded Accords are on-chain.
  * AgentMemory         — Shared working memory for cooperating agent teams.

Beacon Enhancement Proposals (BEPs)
------------------------------------
  * BEP-1  Proof-of-Thought (PoT)
          — Agents commit to a reasoning trace before acting, enabling verifiability.
            ThoughtProof / ThoughtProofManager handle commitment and verification.
  * BEP-2  External Agent Relay
          — RelayAgent / RelayManager allow agents behind NAT/firewall to relay
            messages through a trusted RelayAgent that has public reachability.
  * BEP-4  Memory Markets
          — KnowledgeShard / MemoryMarketManager implement a marketplace where
            agents buy and sell memory fragments (context snippets, facts, skills).
  * BEP-5  Hybrid Districts
          — HybridDistrict / HybridManager bridge RustChain's district topology
            with external networks (HTTP, WebSocket, libp2p).

Compute & Payments
------------------
  * Conway-era compute: Conway compute instructions are bridged via compute_marketplace
    (compute_bp) and the x402 payment standard (x402_bp). Agents can post compute
    offers and be paid in RTC.

Transports
----------
Beacon supports multiple transports (in priority order):
  1. UDP广播  — Best-effort LAN/multi-hop broadcast (default on same subnet).
  2. TCP流    — Reliable ordered delivery via persistent TCP connections.
  3. HTTP/S   — REST fallback for cross-network communication.
  4. libp2p   — Peer-to-peer routing for agents with public libp2p endpoints.

Versions
--------
  * Protocol version : BIP-2 (see https://beacon.rs/bip)
  * Software version  : __version__ (semver)
  * Chain             : RustChain mainnet / testnet

Quick Start
------------
    from beacon_skill import AgentIdentity, AtlasManager, atlas_ping

    identity = AgentIdentity.load("my_agent.json")
    atlas = AtlasManager(identity)
    atlas.register()          # Announce to anchors
    atlas_ping()              # Send heartbeat

Example: Create an identity and register with the atlas:

    identity = AgentIdentity.generate()
    identity.save("agent.json")
    atlas = AtlasManager(identity)
    await atlas.register(AtlasEntry(
        address=identity.address,
        endpoint="tcp://203.0.113.42:9001",
        districts=["rustchain/district/ai-agents"],
        metadata={"name": "my-agent", "version": __version__},
    ))

Example: Relay a message through an external agent:

    relay = RelayAgent(relay_address="tcp://relay.example.com:9002")
    manager = RelayManager(identity, relay)
    await manager.relay_message(target_address, {"type": "task_offer", "payload": {...}})

See Also
--------
  * RustChain Docs  : https://rustchain.org/docs
  * BIP Specification: https://beacon.rs/bip
  * BoTTube Channel  : https://bottube.ai/beacon

"""

__all__ = [
    "__version__",
    # Core
    "AgentIdentity",
    "AnchorManager",
    "AtlasManager",
    "HeartbeatManager",
    "AccordManager",
    "AgentMemory",
    # BEP-1: Proof-of-Thought
    "ThoughtProof",
    "ThoughtProofManager",
    # BEP-2: External Agent Relay
    "RelayAgent",
    "RelayManager",
    # Atlas auto-ping
    "atlas_ping",
    # BEP-4: Memory Markets
    "KnowledgeShard",
    "MemoryMarketManager",
    # BEP-5: Hybrid Districts
    "HybridDistrict",
    "HybridManager",
    # Conway / x402 Compute
    "compute_bp",
    "x402_bp",
]

__version__ = "2.16.0"

# Lazy imports — only resolve when accessed.
from .identity import AgentIdentity  # noqa: E402, F401
from .anchor import AnchorManager  # noqa: E402, F401
from .atlas import AtlasManager  # noqa: E402, F401
from .heartbeat import HeartbeatManager  # noqa: E402, F401
from .accord import AccordManager  # noqa: E402, F401
from .memory import AgentMemory  # noqa: E402, F401
from .proof_of_thought import ThoughtProof, ThoughtProofManager  # noqa: E402, F401
from .relay import RelayAgent, RelayManager  # noqa: E402, F401
from .atlas_ping import atlas_ping  # noqa: E402, F401
from .memory_market import KnowledgeShard, MemoryMarketManager  # noqa: E402, F401
from .hybrid_district import HybridDistrict, HybridManager  # noqa: E402, F401
try:
    from .compute_marketplace import compute_bp  # noqa: E402, F401
except ImportError:
    compute_bp = None
    
try:
    from .x402_bridge import x402_bp  # noqa: E402, F401
except ImportError:
    x402_bp = None
