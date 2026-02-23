#!/usr/bin/env python3
"""
Bounty Scanner - Monitor rustchain-bounties for new issues

This script checks the rustchain-bounties repository for new open issues
and prints them. Useful for staying up-to-date with new bounty opportunities.

Usage:
    python bounty_scanner.py

Requirements:
    pip install requests

Author: Eve (Beacon Agent)
"""

import json
import time
import os
from datetime import datetime, timezone

# Try to import beacon_skill modules, fall back to requests if not available
try:
    from beacon_skill import atlas
    from beacon_skill.config import load_config
    BEACON_SKILL_AVAILABLE = True
except ImportError:
    BEACON_SKILL_AVAILABLE = False
    import requests

# Configuration
GITHUB_API_URL = "https://api.github.com/repos/Scottcjn/rustchain-bounties/issues"
REPO_URL = "https://github.com/Scottcjn/rustchain-bounties/issues"
STATE_FILE = os.path.expanduser("~/.beacon_bounty_scanner_state.json")


def get_last_checked():
    """Load last checked issue number from state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                return data.get('last_issue_number', 0)
        except (json.JSONDecodeError, IOError):
            pass
    return 0


def save_last_checked(issue_number):
    """Save last checked issue number to state file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_issue_number': issue_number, 'last_check': datetime.now().isoformat()}, f)
    except IOError:
        pass


def fetch_issues(state="open", per_page=10):
    """Fetch issues from rustchain-bounties repository."""
    if BEACON_SKILL_AVAILABLE:
        # Use beacon_skill if available
        try:
            import requests
            resp = requests.get(
                GITHUB_API_URL,
                params={"state": state, "per_page": per_page, "sort": "created", "direction": "desc"},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Warning: beacon_skill request failed, falling back to requests: {e}")
    
    # Fallback to requests
    import requests
    resp = requests.get(
        GITHUB_API_URL,
        params={"state": state, "per_page": per_page, "sort": "created", "direction": "desc"},
        headers={"Accept": "application/vnd.github.v3+json"},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def format_issue(issue):
    """Format an issue for display."""
    number = issue.get('number', 0)
    title = issue.get('title', 'No title')
    url = issue.get('html_url', REPO_URL)
    labels = [l.get('name', '') for l in issue.get('labels', [])]
    created_at = issue.get('created_at', '')
    
    # Parse datetime
    try:
        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        age = (now - dt.replace(tzinfo=timezone.utc)).days
        age_str = f"{age}d ago" if age > 0 else "today"
    except:
        age_str = "unknown"
    
    label_str = f" [{', '.join(labels)}]" if labels else ""
    
    return f"""#{number}: {title}{label_str}
   {url} ({age_str})"""


def scan_for_new_bounties():
    """Main scanning function."""
    print("🔍 Scanning rustchain-bounties for new opportunities...")
    print("=" * 60)
    
    last_checked = get_last_checked()
    print(f"Last checked issue: #{last_checked}")
    print()
    
    try:
        issues = fetch_issues()
    except Exception as e:
        print(f"❌ Error fetching issues: {e}")
        return False
    
    if not issues:
        print("No issues found.")
        return True
    
    new_issues = []
    for issue in issues:
        if issue.get('number', 0) > last_checked:
            new_issues.append(issue)
    
    # Update last checked
    if issues:
        save_last_checked(issues[0].get('number', 0))
    
    if new_issues:
        print(f"🚨 Found {len(new_issues)} NEW issue(s)!")
        print("-" * 60)
        for issue in new_issues:
            print(format_issue(issue))
            print()
    else:
        print("✅ No new issues since last check.")
        print("-" * 60)
        print("Recent issues:")
        for issue in issues[:5]:
            print(format_issue(issue))
            print()
    
    return True


def list_all_bounties():
    """List all open bounties."""
    print("📋 All Open Bounties")
    print("=" * 60)
    
    try:
        issues = fetch_issues(per_page=30)
    except Exception as e:
        print(f"❌ Error fetching issues: {e}")
        return
    
    for issue in issues:
        print(format_issue(issue))
        print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_all_bounties()
    else:
        scan_for_new_bounties()
