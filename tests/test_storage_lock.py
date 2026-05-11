from beacon_skill import storage


def test_state_lock_uses_windows_locking_fallback(monkeypatch, tmp_path):
    calls = []

    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        def locking(self, fileno, mode, nbytes):
            calls.append((mode, nbytes))

    monkeypatch.setattr(storage, "_dir", lambda: tmp_path)
    monkeypatch.setattr(storage, "_fcntl", None)
    monkeypatch.setattr(storage, "_msvcrt", FakeMsvcrt())

    with storage.state_lock(write=True):
        assert (tmp_path / "state.json.lock").exists()

    assert calls == [(FakeMsvcrt.LK_LOCK, 1), (FakeMsvcrt.LK_UNLCK, 1)]
