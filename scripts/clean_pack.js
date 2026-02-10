#!/usr/bin/env node
/**
 * Remove Python bytecode caches before npm pack/publish.
 * These files are environment-specific and just bloat the tarball.
 */

const fs = require('fs');
const path = require('path');

function rmrf(p) {
  try {
    fs.rmSync(p, { recursive: true, force: true });
  } catch (_) {}
}

function walk(dir) {
  let entries = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (_) {
    return;
  }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === '__pycache__') {
        rmrf(p);
        continue;
      }
      walk(p);
      continue;
    }
    if (e.isFile()) {
      if (e.name.endsWith('.pyc') || e.name.endsWith('.pyo') || e.name.endsWith('.pyd')) {
        try { fs.unlinkSync(p); } catch (_) {}
      }
    }
  }
}

walk(path.join(__dirname, '..', 'beacon_skill'));

