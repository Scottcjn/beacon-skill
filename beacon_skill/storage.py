import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


def _dir() -> Path:
    d = Path.home() / ".beacon"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def state_lock(write: bool = False):
    """Context manager for advisory file locking on the state file."""
    path = _dir() / "state.json.lock"
    # Ensure lock file exists
    if not path.exists():
        path.touch()
    
    # We use a separate lock file to avoid issues with opening/closing the JSON file itself
    with path.open("w") as f:
        # LOCK_EX for write, LOCK_SH for read
        mode = fcntl.LOCK_EX if write else fcntl.LOCK_SH
        try:
            fcntl.flock(f, mode)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _safe_path(name: str) -> Path:
    """Resolve a storage name to a path, preventing directory traversal."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"Invalid storage name: {name!r}")
    path = (_dir() / name).resolve()
    base = _dir().resolve()
    if not str(path).startswith(str(base)):
        raise ValueError(f"Path traversal detected: {name!r}")
    return path


def append_jsonl(name: str, item: Dict[str, Any]) -> None:
    path = _safe_path(name)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, sort_keys=True) + "\n")


def read_jsonl(name: str) -> List[Dict[str, Any]]:
    """Read all entries from a JSONL file."""
    path = _safe_path(name)
    if not path.exists():
        return []
    results = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except Exception:
            continue
    return results


def read_state() -> Dict[str, Any]:
    path = _dir() / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(state: Dict[str, Any]) -> None:
    path = _dir() / "state.json"
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_last_ts(key: str) -> Optional[float]:
    state = read_state()
    v = state.get("last_ts", {}).get(key)
    try:
        return float(v)
    except Exception:
        return None


def set_last_ts(key: str, ts: Optional[float] = None) -> None:
    state = read_state()
    state.setdefault("last_ts", {})
    state["last_ts"][key] = float(ts if ts is not None else time.time())
    write_state(state)


def jsonl_count(name: str) -> int:
    """Count entries in a JSONL file."""
    path = _safe_path(name)
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            count += 1
    return count


def read_jsonl_tail(name: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """Read the last N entries from a JSONL file efficiently."""
    path = _safe_path(name)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    results = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except Exception:
            continue
    return results


def read_json(name: str) -> Dict[str, Any]:
    """Read a JSON file from the beacon directory."""
    path = _safe_path(name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(name: str, data: Dict[str, Any]) -> None:
    """Write a JSON file to the beacon directory."""
    path = _safe_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
