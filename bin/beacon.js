#!/usr/bin/env node
/**
 * Beacon (npm) - thin Node wrapper that runs the Python Beacon CLI in a venv.
 *
 * Why: keep one implementation (Python) while still shipping an npm-installable
 * `beacon` command for agents that live in Node land.
 */

const { spawnSync, execSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const PKG_ROOT = path.join(__dirname, '..');
let VERSION = '1.0.0';
try {
  const pkg = require(path.join(PKG_ROOT, 'package.json'));
  if (pkg && pkg.version) VERSION = pkg.version;
} catch (_) {}

const INSTALL_DIR = path.join(os.homedir(), '.beacon', 'npm');
const VENV_DIR = path.join(INSTALL_DIR, 'venv');
const MARKER = path.join(INSTALL_DIR, '.deps_ok');

function log(msg) {
  process.stderr.write(`[beacon] ${msg}\n`);
}

function pythonBin() {
  if (os.platform() === 'win32') {
    return path.join(VENV_DIR, 'Scripts', 'python.exe');
  }
  return path.join(VENV_DIR, 'bin', 'python');
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function haveVenv() {
  return fs.existsSync(pythonBin());
}

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (r.error) throw r.error;
  if (typeof r.status === 'number' && r.status !== 0) {
    process.exit(r.status);
  }
}

function ensureVenv() {
  if (haveVenv()) return;

  ensureDir(INSTALL_DIR);
  log(`Creating venv at ${VENV_DIR}`);
  run('python3', ['-m', 'venv', VENV_DIR]);
}

function ensureDeps() {
  if (fs.existsSync(MARKER)) return;

  const py = pythonBin();
  log('Installing python deps (requests, cryptography)');

  // Ensure pip exists/up to date enough to fetch wheels.
  run(py, ['-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel'], {
    env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: '1' },
  });
  run(py, ['-m', 'pip', 'install', 'requests>=2.25', 'cryptography>=41'], {
    env: { ...process.env, PIP_DISABLE_PIP_VERSION_CHECK: '1' },
  });

  fs.writeFileSync(MARKER, `ok ${VERSION}\n`);
}

function main() {
  // Fast path: avoid network installs for a simple version check.
  if (process.argv.slice(2).includes('--version')) {
    process.stdout.write(`${VERSION}\n`);
    return;
  }

  ensureVenv();
  ensureDeps();

  const py = pythonBin();
  const code = 'from beacon_skill.cli import main; main()';
  const args = ['-c', code, ...process.argv.slice(2)];

  const env = { ...process.env };
  // Allow importing the Python package directly from the npm install directory.
  const pp = env.PYTHONPATH ? `${PKG_ROOT}${path.delimiter}${env.PYTHONPATH}` : PKG_ROOT;
  env.PYTHONPATH = pp;

  const r = spawnSync(py, args, { stdio: 'inherit', env });
  process.exit(typeof r.status === 'number' ? r.status : 1);
}

try {
  main();
} catch (e) {
  log(String(e && e.message ? e.message : e));
  process.exit(1);
}
