import builtins
import importlib.util
import sys
from pathlib import Path


def test_storage_imports_and_writes_jsonl_without_fcntl(tmp_path, monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "beacon_skill" / "storage.py"
    original_import = builtins.__import__

    def import_without_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("No module named 'fcntl'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_fcntl)
    spec = importlib.util.spec_from_file_location(
        "beacon_skill_storage_no_fcntl", module_path
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    monkeypatch.setenv("BEACON_INBOX_PATH", str(tmp_path / "inbox.jsonl"))
    monkeypatch.setattr(module, "_dir", lambda: tmp_path)

    assert module._HAVE_FCNTL is False
    with module.state_lock(write=True):
        module.append_jsonl("inbox.jsonl", {"message": "windows fallback"})

    assert module.read_jsonl("inbox.jsonl") == [{"message": "windows fallback"}]
