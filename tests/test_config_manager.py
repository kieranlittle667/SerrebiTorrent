import json
import pytest

import config_manager


def _configure_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    legacy_path = tmp_path / "legacy_config.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(config_path))
    monkeypatch.setattr(config_manager, "LEGACY_CONFIG_FILE", str(legacy_path))
    return config_path, legacy_path


def test_load_config_handles_corrupt_json(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    corrupt = "{bad json"
    config_path.write_text(corrupt, encoding="utf-8")
    cm = config_manager.ConfigManager()
    prefs = cm.get_preferences()
    assert "download_path" in prefs
    assert prefs["language"] == "system"
    assert cm.get_profiles()
    assert config_path.read_text(encoding="utf-8") == corrupt


def test_load_config_migrates_legacy(tmp_path, monkeypatch):
    config_path, legacy_path = _configure_paths(tmp_path, monkeypatch)
    legacy_path.write_text(
        json.dumps({"preferences": {"download_path": "C:\\Downloads"}, "profiles": {}}),
        encoding="utf-8",
    )
    cm = config_manager.ConfigManager()
    assert cm.get_preferences().get("download_path") == "C:\\Downloads"
    assert cm.get_preferences().get("language") == "system"
    assert config_path.exists()


def test_normalize_prefs_missing_keys(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(json.dumps({"preferences": {"download_path": "C:\\X"}, "profiles": {}}), encoding="utf-8")
    cm = config_manager.ConfigManager()
    prefs = cm.get_preferences()
    assert prefs.get("download_path") == "C:\\X"
    assert "web_ui_port" in prefs
    assert prefs.get("language") == "system"
    assert prefs["clipboard_prefill"] is True


def test_clipboard_prefill_preference_persists_independently(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    cm.set_preferences({"clipboard_prefill": False, "clipboard_auto_add": True})
    saved = config_manager.ConfigManager().get_preferences()
    assert saved["clipboard_prefill"] is False
    assert saved["clipboard_auto_add"] is True


def test_listen_interface_default_is_blank(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    assert cm.get_preferences().get("listen_interface") == ""


def test_explicit_language_preference_is_preserved(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(
        json.dumps({"preferences": {"language": "pt-BR"}, "profiles": {}}),
        encoding="utf-8",
    )

    cm = config_manager.ConfigManager()

    assert cm.get_preferences()["language"] == "pt-BR"


def test_get_profiles_returns_copy(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(
        json.dumps({
            "preferences": {},
            "profiles": {"p1": {"name": "Original", "type": "local", "url": "C:\\X", "user": "", "password": ""}},
            "default_profile": "p1",
        }),
        encoding="utf-8",
    )
    cm = config_manager.ConfigManager()
    profiles = cm.get_profiles()
    profiles["p1"]["name"] = "Mutated"

    assert cm.get_profile("p1")["name"] == "Original"


def test_load_config_repairs_blank_default_profile(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(
        json.dumps({
            "preferences": {},
            "profiles": {"p1": {"name": "Local", "type": "local", "url": "C:\\X", "user": "", "password": ""}},
            "default_profile": "",
        }),
        encoding="utf-8",
    )

    cm = config_manager.ConfigManager()

    assert cm.get_default_profile_id() == "p1"


def test_load_config_repairs_invalid_default_profile(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(
        json.dumps({
            "preferences": {},
            "profiles": {
                "p1": {"name": "One", "type": "local", "url": "C:\\One", "user": "", "password": ""},
                "p2": {"name": "Two", "type": "local", "url": "C:\\Two", "user": "", "password": ""},
            },
            "default_profile": "missing",
        }),
        encoding="utf-8",
    )

    cm = config_manager.ConfigManager()

    assert cm.get_default_profile_id() == "p1"


def test_delete_default_profile_selects_remaining_profile(tmp_path, monkeypatch):
    config_path, _ = _configure_paths(tmp_path, monkeypatch)
    config_path.write_text(
        json.dumps({
            "preferences": {},
            "profiles": {
                "p1": {"name": "One", "type": "local", "url": "C:\\One", "user": "", "password": ""},
                "p2": {"name": "Two", "type": "local", "url": "C:\\Two", "user": "", "password": ""},
            },
            "default_profile": "p1",
        }),
        encoding="utf-8",
    )
    cm = config_manager.ConfigManager()

    cm.delete_profile("p1")

    assert cm.get_default_profile_id() == "p2"


def test_set_preferences_rolls_back_on_save_failure(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    before = cm.get_preferences()

    monkeypatch.setattr(cm, "save_config", lambda: (_ for _ in ()).throw(OSError("disk full")))

    try:
        cm.set_preferences({"download_path": "C:\\Broken", "language": "pt-BR"})
    except OSError:
        pass
    else:
        raise AssertionError("Expected set_preferences to propagate persistence failure")

    assert cm.get_preferences() == before


def test_add_profile_rolls_back_on_save_failure(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    before = cm.get_profiles()

    monkeypatch.setattr(cm, "save_config", lambda: (_ for _ in ()).throw(OSError("disk full")))

    try:
        cm.add_profile("Remote", "qbittorrent", "http://localhost:8080", "user", "secret")
    except OSError:
        pass
    else:
        raise AssertionError("Expected add_profile to propagate persistence failure")

    assert cm.get_profiles() == before


def test_update_profile_rolls_back_on_save_failure(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    pid = cm.get_default_profile_id()
    before = cm.get_profile(pid)

    monkeypatch.setattr(cm, "save_config", lambda: (_ for _ in ()).throw(OSError("disk full")))

    try:
        cm.update_profile(pid, "Changed", "local", "C:\\Changed", "", "")
    except OSError:
        pass
    else:
        raise AssertionError("Expected update_profile to propagate persistence failure")

    assert cm.get_profile(pid) == before


def test_delete_profile_rolls_back_on_save_failure(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    pid = cm.add_profile("Remote", "qbittorrent", "http://localhost:8080", "user", "secret")
    before_profiles = cm.get_profiles()
    before_default = cm.get_default_profile_id()

    monkeypatch.setattr(cm, "save_config", lambda: (_ for _ in ()).throw(OSError("disk full")))

    try:
        cm.delete_profile(pid)
    except OSError:
        pass
    else:
        raise AssertionError("Expected delete_profile to propagate persistence failure")

    assert cm.get_profiles() == before_profiles
    assert cm.get_default_profile_id() == before_default


def test_default_profile_rolls_back_on_save_failure(tmp_path, monkeypatch):
    _configure_paths(tmp_path, monkeypatch)
    cm = config_manager.ConfigManager()
    original = cm.get_default_profile_id()
    other = cm.add_profile("Remote", "qbittorrent", "http://localhost:8080", "user", "secret")

    monkeypatch.setattr(cm, "save_config", lambda: (_ for _ in ()).throw(OSError("disk full")))

    try:
        cm.set_default_profile_id(other)
    except OSError:
        pass
    else:
        raise AssertionError("Expected set_default_profile_id to propagate persistence failure")

    assert cm.get_default_profile_id() == original



def test_corrupt_current_config_is_not_overwritten_by_legacy_fallback(tmp_path, monkeypatch):
    config_path, legacy_path = _configure_paths(tmp_path, monkeypatch)
    corrupt = "{broken current"
    config_path.write_text(corrupt, encoding="utf-8")
    legacy_path.write_text(
        json.dumps({
            "preferences": {"download_path": "C:\\Legacy"},
            "profiles": {},
        }),
        encoding="utf-8",
    )

    cm = config_manager.ConfigManager()

    assert cm.get_preferences()["download_path"] == "C:\\Legacy"
    assert config_path.read_text(encoding="utf-8") == corrupt

    # A later preference change must not silently replace the damaged file.
    with pytest.raises(OSError, match="unreadable"):
        cm.set_preferences({**cm.get_preferences(), "language": "en"})
    assert config_path.read_text(encoding="utf-8") == corrupt

    config_path.unlink()
    cm.set_preferences({**cm.get_preferences(), "language": "en"})
    assert json.loads(config_path.read_text(encoding="utf-8"))["preferences"]["language"] == "en"



def test_write_json_restricts_permissions_on_posix(tmp_path, monkeypatch):
    import config_manager

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager.os, "name", "posix", raising=False)
    calls = []
    monkeypatch.setattr(config_manager.os, "chmod", lambda p, mode: calls.append((p, mode)))

    config_manager._write_json(str(path), {"password": "secret"})

    assert calls == [(str(path), 0o600)]


def test_write_json_ignores_chmod_failure_after_successful_replace(tmp_path, monkeypatch):
    import config_manager

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_manager.os, "name", "posix", raising=False)

    def fail_chmod(*args, **kwargs):
        raise OSError("no chmod")

    monkeypatch.setattr(config_manager.os, "chmod", fail_chmod)

    config_manager._write_json(str(path), {"ok": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}



def test_read_json_rejects_oversized_config(tmp_path, monkeypatch):
    import config_manager as cm

    path = tmp_path / "config.json"
    path.write_bytes(b"{}" + b" " * 7)
    monkeypatch.setattr(cm, "CONFIG_MAX_BYTES", 8)

    with pytest.raises(ValueError, match="exceeds the allowed size"):
        cm._read_json(str(path))


def test_read_json_accepts_config_at_limit(tmp_path, monkeypatch):
    import config_manager as cm

    path = tmp_path / "config.json"
    path.write_bytes(b'{"a":1}')
    monkeypatch.setattr(cm, "CONFIG_MAX_BYTES", len(path.read_bytes()))

    assert cm._read_json(str(path)) == {"a": 1}
