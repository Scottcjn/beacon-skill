# Beacon Atlas Join Endpoint Fix - Issue #2127

## Problem
- `POST /beacon/join` returns 404
- `rustchain.org/beacon/atlas` returns 404

## Root Cause
The nginx configuration for rustchain.org was not properly deployed or the beacon_chat.py service was not running.

## Solution

### 1. Start Beacon Atlas Service
```bash
# Copy systemd service file
cp atlas/beacon_atlas.service /etc/systemd/system/

# Enable and start
systemctl daemon-reload
systemctl enable beacon_atlas
systemctl start beacon_atlas

# Verify running
systemctl status beacon_atlas
curl http://127.0.0.1:8071/beacon/join -X POST -H "Content-Type: application/json" -d '{"agent_id":"test","pubkey_hex":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}'
```

### 2. Deploy Nginx Configuration
```bash
# Copy nginx config
cp atlas/nginx_rustchain_org.conf /etc/nginx/sites-available/rustchain.org

# Enable site
ln -sf /etc/nginx/sites-available/rustchain.org /etc/nginx/sites-enabled/

# Test and reload
nginx -t
systemctl reload nginx
```

### 3. Verify
```bash
# Test API endpoint
curl -X POST https://rustchain.org/beacon/join \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"test-agent","pubkey_hex":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}'

# Test UI endpoint
curl https://rustchain.org/beacon/atlas
```

## Files Added
- `atlas/beacon_atlas.service` - systemd service configuration
- `atlas/DEPLOY_FIX_2127.md` - this deployment guide

## Bounty Claim
**Issue**: #2127 (25 RTC)
**Wallet**: RTCb72a1accd46b9ba9f22dbd4b5c6aad5a5831572b
