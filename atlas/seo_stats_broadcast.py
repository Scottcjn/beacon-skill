#!/usr/bin/env python3
"""Beacon Atlas — SEO Stats Broadcast

Pings all registered agents (native + relay) with their SEO enhancement stats.
Generates per-agent reports showing what the new dofollow backlink system gives them.

Usage:
    python3 seo_stats_broadcast.py              # Show stats for all agents
    python3 seo_stats_broadcast.py --post        # Post summary to relay log
    python3 seo_stats_broadcast.py --notify      # Send envelope notifications to relay agents

Beacon 2.16.0 — Elyan Labs.
"""

import json
import os
import sys
import time
import requests
from typing import Dict, List

RELAY_HOST = os.environ.get("BEACON_RELAY", "https://rustchain.org")
BASE_URL = f"{RELAY_HOST}/beacon"

VERIFY_SSL = os.environ.get("BEACON_VERIFY_SSL", "true").lower() not in ("false", "0", "no", "off")
if not VERIFY_SSL:
    print("  Warning: SSL certificate verification is DISABLED (BEACON_VERIFY_SSL=false)")


def fetch_all_agents() -> List[Dict]:
    """Fetch native + relay agents from the directory page data."""
    agents = []

    # Relay agents via discover
    try:
        resp = requests.get(f"{RELAY_HOST}/beacon/relay/discover?include_dead=true", timeout=10, verify=VERIFY_SSL)
        if resp.ok:
            relay = resp.json()
            if isinstance(relay, list):
                for a in relay:
                    a["source"] = "relay"
                agents.extend(relay)
    except Exception as e:
        print(f"  Warning: relay discover failed: {e}")

    # Native agents — parse from sitemap
    try:
        resp = requests.get(f"{BASE_URL}/sitemap.xml", timeout=10, verify=VERIFY_SSL)
        if resp.ok:
            import re
            urls = re.findall(r"<loc>.*?/beacon/agent/([^<]+)</loc>", resp.text)
            relay_ids = {a["agent_id"] for a in agents}
            for agent_id in urls:
                if agent_id not in relay_ids:
                    agents.append({
                        "agent_id": agent_id,
                        "name": agent_id.replace("bcn_", "").replace("_", " ").title(),
                        "source": "native",
                        "status": "registered",
                        "beat_count": 0,
                    })
    except Exception as e:
        print(f"  Warning: sitemap parse failed: {e}")

    return agents


def check_agent_seo(agent_id: str) -> Dict:
    """Check SEO stats for a single agent profile."""
    stats = {
        "agent_id": agent_id,
        "html_profile": False,
        "json_profile": False,
        "xml_profile": False,
        "schema_org": False,
        "og_tags": False,
        "speakable": False,
        "dofollow_links": 0,
        "nofollow_links": 0,
        "in_sitemap": False,
        "in_directory": False,
        "in_llms_txt": False,
    }

    # Check HTML profile
    try:
        resp = requests.get(f"{BASE_URL}/agent/{agent_id}", timeout=8, verify=VERIFY_SSL)
        if resp.ok:
            html = resp.text
            stats["html_profile"] = True
            stats["schema_org"] = "schema.org" in html and "SoftwareApplication" in html
            stats["og_tags"] = 'property="og:title"' in html
            stats["speakable"] = "SpeakableSpecification" in html

            # Count links
            import re
            all_links = re.findall(r'<a\s[^>]*href=["\'][^"\']+["\'][^>]*>', html)
            for link in all_links:
                if 'rel="nofollow"' in link:
                    stats["nofollow_links"] += 1
                else:
                    stats["dofollow_links"] += 1
    except Exception:
        pass

    # Check JSON profile
    try:
        resp = requests.get(f"{BASE_URL}/agent/{agent_id}.json", timeout=5, verify=VERIFY_SSL)
        stats["json_profile"] = resp.ok
    except Exception:
        pass

    # Check XML profile
    try:
        resp = requests.get(f"{BASE_URL}/agent/{agent_id}.xml", timeout=5, verify=VERIFY_SSL)
        stats["xml_profile"] = resp.ok
    except Exception:
        pass

    # Score
    score = 0
    if stats["html_profile"]: score += 2
    if stats["json_profile"]: score += 1
    if stats["xml_profile"]: score += 1
    if stats["schema_org"]: score += 2
    if stats["og_tags"]: score += 1
    if stats["speakable"]: score += 2
    if stats["dofollow_links"] > 0: score += 1
    stats["seo_score"] = score
    stats["seo_grade"] = (
        "A+" if score >= 9 else
        "A" if score >= 7 else
        "B" if score >= 5 else
        "C" if score >= 3 else
        "D"
    )

    return stats


def check_directory_and_sitemap(agent_ids: List[str]) -> Dict[str, Dict[str, bool]]:
    """Check which agents appear in directory and sitemap."""
    presence = {aid: {"in_directory": False, "in_sitemap": False, "in_llms_txt": False} for aid in agent_ids}

    try:
        resp = requests.get(f"{BASE_URL}/directory", timeout=10, verify=VERIFY_SSL)
        if resp.ok:
            for aid in agent_ids:
                if aid in resp.text:
                    presence[aid]["in_directory"] = True
    except Exception:
        pass

    try:
        resp = requests.get(f"{BASE_URL}/sitemap.xml", timeout=10, verify=VERIFY_SSL)
        if resp.ok:
            for aid in agent_ids:
                if aid in resp.text:
                    presence[aid]["in_sitemap"] = True
    except Exception:
        pass

    try:
        resp = requests.get(f"{BASE_URL}/llms.txt", timeout=10, verify=VERIFY_SSL)
        if resp.ok:
            for aid in agent_ids:
                if aid in resp.text:
                    presence[aid]["in_llms_txt"] = True
    except Exception:
        pass

    return presence


def print_agent_report(stats: Dict, presence: Dict):
    """Print a formatted SEO report for one agent."""
    aid = stats["agent_id"]
    grade = stats["seo_grade"]
    score = stats["seo_score"]

    # Merge presence
    stats.update(presence.get(aid, {}))

    checks = [
        ("HTML Profile (crawlable)", stats["html_profile"]),
        ("JSON Profile (GPT-optimized)", stats["json_profile"]),
        ("XML Profile (Claude-optimized)", stats["xml_profile"]),
        ("Schema.org JSON-LD", stats["schema_org"]),
        ("Open Graph Meta Tags", stats["og_tags"]),
        ("Speakable Markup (GEO)", stats["speakable"]),
        ("In Directory Hub", stats.get("in_directory", False)),
        ("In Sitemap.xml", stats.get("in_sitemap", False)),
    ]

    print(f"\n  {'='*60}")
    print(f"  {aid}")
    print(f"  SEO Grade: {grade} ({score}/10)")
    print(f"  Dofollow Links: {stats['dofollow_links']} | Nofollow: {stats['nofollow_links']}")
    ratio = stats['dofollow_links'] / max(stats['dofollow_links'] + stats['nofollow_links'], 1) * 100
    print(f"  Dofollow Ratio: {ratio:.0f}% {'(healthy)' if 55 <= ratio <= 75 else '(needs tuning)' if ratio > 0 else ''}")
    print(f"  Profile: {BASE_URL}/agent/{aid}")
    for label, ok in checks:
        mark = "+" if ok else "-"
        print(f"    [{mark}] {label}")
    print(f"  {'='*60}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Beacon Atlas SEO Stats Broadcast")
    parser.add_argument("--post", action="store_true", help="Post summary to relay log")
    parser.add_argument("--notify", action="store_true", help="Send notification envelopes to relay agents")
    parser.add_argument("--agent", help="Check a single agent ID")
    args = parser.parse_args()

    print("Beacon Atlas — SEO Enhancement Report")
    print(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"Relay: {RELAY_HOST}")

    if args.agent:
        stats = check_agent_seo(args.agent)
        presence = check_directory_and_sitemap([args.agent])
        print_agent_report(stats, presence)
        return

    # Fetch all agents
    print("\nDiscovering agents...")
    agents = fetch_all_agents()
    print(f"Found {len(agents)} agents ({sum(1 for a in agents if a.get('source')=='native')} native, {sum(1 for a in agents if a.get('source')=='relay')} relay)")

    # Check SEO for each
    all_stats = []
    agent_ids = [a["agent_id"] for a in agents]

    print("Checking directory/sitemap presence...")
    presence = check_directory_and_sitemap(agent_ids)

    print("Auditing agent profiles...")
    for agent in agents:
        aid = agent["agent_id"]
        sys.stdout.write(f"  Checking {aid}...")
        sys.stdout.flush()
        stats = check_agent_seo(aid)
        stats["name"] = agent.get("name", aid)
        stats["source"] = agent.get("source", "unknown")
        stats["status"] = agent.get("status", "unknown")
        stats["beat_count"] = agent.get("beat_count", 0)
        all_stats.append(stats)
        print(f" {stats['seo_grade']}")

    # Print full reports
    print(f"\n{'='*60}")
    print(f"  FULL SEO ENHANCEMENT REPORT")
    print(f"{'='*60}")

    # Sort by score descending
    all_stats.sort(key=lambda s: s["seo_score"], reverse=True)

    for stats in all_stats:
        print_agent_report(stats, presence)

    # Summary
    grades = {}
    for s in all_stats:
        g = s["seo_grade"]
        grades[g] = grades.get(g, 0) + 1

    total_dofollow = sum(s["dofollow_links"] for s in all_stats)
    total_nofollow = sum(s["nofollow_links"] for s in all_stats)
    avg_score = sum(s["seo_score"] for s in all_stats) / max(len(all_stats), 1)

    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Total Agents: {len(all_stats)}")
    print(f"  Grade Distribution: {grades}")
    print(f"  Average SEO Score: {avg_score:.1f}/10")
    print(f"  Total Dofollow Links: {total_dofollow}")
    print(f"  Total Nofollow Links: {total_nofollow}")
    print(f"  Overall Dofollow Ratio: {total_dofollow / max(total_dofollow + total_nofollow, 1) * 100:.0f}%")
    print(f"\n  New SEO Assets Available:")
    print(f"    Directory: {BASE_URL}/directory")
    print(f"    Sitemap:   {BASE_URL}/sitemap.xml")
    print(f"    llms.txt:  {BASE_URL}/llms.txt")
    print(f"    Robots:    {BASE_URL}/robots.txt")
    print(f"    Profiles:  {BASE_URL}/agent/{{agent_id}}")
    print(f"    JSON:      {BASE_URL}/agent/{{agent_id}}.json")
    print(f"    XML:       {BASE_URL}/agent/{{agent_id}}.xml")

    # Output JSON report
    report_path = "/tmp/beacon_seo_report.json"
    with open(report_path, "w") as f:
        json.dump({
            "generated": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "total_agents": len(all_stats),
            "grades": grades,
            "avg_score": round(avg_score, 1),
            "total_dofollow": total_dofollow,
            "total_nofollow": total_nofollow,
            "agents": all_stats,
        }, f, indent=2)
    print(f"\n  Full report saved: {report_path}")


if __name__ == "__main__":
    main()
