#!/usr/bin/env python3
"""
Beacon Skill - RustChain Payment Example

This example demonstrates how to:
1. Create a new RustChain wallet
2. Check wallet balance
3. Send RTC payments to another wallet

Usage:
    python examples/rustchain_payment.py

Requirements:
    - beacon-skill installed (pip install beacon-skill[mnemonic])
    - Identity created (beacon identity new)
    - Configure wallet in ~/.beacon/config.json

Config Example:
    {
        "rustchain": {
            "private_key_hex": "your_private_key_here",
            "base_url": "https://rustchain.org"
        }
    }
"""

import json
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from beacon_skill import Beacon


def load_config():
    """Load beacon configuration."""
    config_path = os.path.expanduser("~/.beacon/config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def main():
    print("=" * 60)
    print("Beacon Skill - RustChain Payment Demo")
    print("=" * 60)
    
    # Initialize beacon
    beacon = Beacon()
    
    # Show identity
    identity = beacon.identity
    print(f"\nAgent ID: {identity.agent_id}")
    print(f"Public Key: {identity.public_key_hex[:32]}...")
    
    # Check config
    config = load_config()
    rustchain_cfg = config.get("rustchain", {})
    
    if not rustchain_cfg.get("private_key_hex"):
        print("\n⚠️  No RustChain wallet configured!")
        print("\nTo set up a wallet:")
        print("1. Create a new wallet:")
        print("   beacon rustchain wallet-new --mnemonic")
        print("\n2. Or configure an existing wallet in ~/.beacon/config.json:")
        print('   {')
        print('     "rustchain": {')
        print('       "private_key_hex": "YOUR_PRIVATE_KEY"')
        print('     }')
        print('   }')
        print("\n3. Get testnet RTC from: https://rustchain.org/faucet")
        return
    
    # Show wallet info
    print("\n" + "-" * 40)
    print("Wallet Configuration:")
    print(f"  Base URL: {rustchain_cfg.get('base_url', 'https://rustchain.org')}")
    print(f"  Private Key: {rustchain_cfg.get('private_key_hex')[:16]}...")
    
    # Check balance
    print("\n" + "-" * 40)
    print("Checking wallet balance...")
    try:
        from beacon_skill.transports.rustchain import RustChainTransport
        transport = RustChainTransport(
            private_key_hex=rustchain_cfg.get("private_key_hex"),
            base_url=rustchain_cfg.get("base_url", "https://rustchain.org"),
            verify_ssl=False
        )
        
        balance = transport.get_balance()
        print(f"✅ Wallet Balance: {balance} RTC")
        
        # Show how to send payment
        print("\n" + "-" * 40)
        print("To send a payment, use:")
        print("  beacon rustchain pay <wallet_address> <amount> --memo '<message>'")
        print("\nOr use the CLI directly:")
        print("  beacon rustchain pay rtc1... 10.5 --memo 'Bounty payment'")
        
    except Exception as e:
        print(f"❌ Error checking balance: {e}")
        print("\nNote: You may need testnet RTC to interact with the network.")
        print("Visit https://rustchain.org/faucet to get test tokens.")


def demo_signed_envelope():
    """Demonstrate creating a signed envelope with payment."""
    print("\n" + "=" * 60)
    print("Signed Envelope Demo")
    print("=" * 60)
    
    beacon = Beacon()
    identity = beacon.identity
    
    # Create a signed envelope with bounty kind
    envelope = beacon.create_envelope(
        kind="bounty",
        text="50 RTC bounty for bug fix",
        agent_id=identity.agent_id
    )
    
    print(f"\nCreated signed envelope:")
    print(f"  Kind: {envelope['kind']}")
    print(f"  Agent ID: {envelope['agent_id']}")
    print(f"  Nonce: {envelope['nonce']}")
    print(f"  Signature: {envelope['sig'][:32]}...")
    
    print("\n✅ Envelope ready for sending via any transport!")
    print("   Use beacon webhook send or beacon udp send to transmit.")


if __name__ == "__main__":
    main()
    demo_signed_envelope()
    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)
