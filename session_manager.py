# ruff: noqa: E402

import os
import threading
import time
import json
import ipaddress
import logging
import shutil
from logging.handlers import RotatingFileHandler

from libtorrent_env import prepare_libtorrent_dlls

prepare_libtorrent_dlls()

try:
    import libtorrent as lt
except ImportError:
    lt = None

from app_paths import get_log_path, get_state_dir
from config_manager import ConfigManager
from torrent_parsing import clean_tracker_urls, normalize_info_hash

# The local session identifies itself to trackers and peers as the current
# qBittorrent release: peer ID -qBXYZ0- and User-Agent qBittorrent/X.Y.Z.
# Plenty of trackers only admit clients from a list they maintain, and a raw
# libtorrent build is usually not on it -- nor is a qBittorrent several
# releases stale. This is the version used when the lookup below has nothing
# newer; qbittorrent_version() keeps it current on its own.
QBITTORRENT_FALLBACK_VERSION = "5.2.3"
QBITTORRENT_RELEASES_URL = (
    "https://api.github.com/repos/qbittorrent/qBittorrent/releases/latest")
# How long a looked-up version is trusted before GitHub is asked again.
VERSION_CHECK_SECONDS = 24 * 3600
VERSION_FETCH_TIMEOUT_S = 10
_VERSION_STATE_FILE = "qbittorrent_version.json"

_version_lock = threading.Lock()

# How often (seconds) to persist resume data (ratio, upload/download totals, etc.)
# in the background. Without this, stats like seeding ratio only survive a graceful
# app shutdown - a crash, force-kill, or update-triggered restart would silently
# roll every torrent back to whatever was last saved, no matter which client
# profile (local/remote) was active in the UI at the time.
AUTOSAVE_INTERVAL_SECONDS = 180
TORRENT_STATE_MAX_BYTES = 16 * 1024 * 1024
RESUME_STATE_MAX_BYTES = 64 * 1024 * 1024
TORRENTS_DB_MAX_BYTES = 16 * 1024 * 1024
VERSION_STATE_MAX_BYTES = 64 * 1024


def _read_bounded_state_file(path, limit, label):
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{label} exceeds the allowed size.")
    return data


def _parse_version(text):
    """(major, minor, patch) out of "release-5.2.3", or None."""
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text or ""))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _version_state_path():
    return os.path.join(get_state_dir(), _VERSION_STATE_FILE)


def _read_version_state():
    try:
        data = _read_bounded_state_file(
            _version_state_path(),
            VERSION_STATE_MAX_BYTES,
            "qBittorrent version state",
        )
        state = json.loads(data.decode("utf-8"))
        if not isinstance(state, dict):
            return None, 0.0
        return _parse_version(state.get("version")), float(state.get("checked", 0) or 0)
    except (OSError, TypeError, ValueError):
        return None, 0.0


def _write_version_state(version):
    path = _version_state_path()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": "%d.%d.%d" % version, "checked": time.time()}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        pass
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _fetch_latest_qbittorrent():
    """Ask GitHub for qBittorrent's newest release tag. None on any failure."""
    try:
        import requests

        response = requests.get(
            QBITTORRENT_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "SerrebiTorrent"},
            timeout=VERSION_FETCH_TIMEOUT_S,
        )
        response.raise_for_status()
        return _parse_version(response.json().get("tag_name"))
    except Exception:
        return None


def qbittorrent_version(allow_network=True):
    """The qBittorrent version this session claims to be, as (x, y, z).

    Looked up from qBittorrent's own releases once a day and remembered in the
    state directory, so the reported version stays current without anybody
    editing a constant before each release -- and so an offline start still
    gets a recent one rather than whatever was last hard-coded.
    """
    with _version_lock:
        cached, checked = _read_version_state()
        fallback = _parse_version(QBITTORRENT_FALLBACK_VERSION) or (5, 2, 3)
        if not allow_network or (cached and time.time() - checked < VERSION_CHECK_SECONDS):
            return cached or fallback
        latest = _fetch_latest_qbittorrent()
        if latest:
            _write_version_state(latest)
            return latest
        return cached or fallback


def qbittorrent_identity(allow_network=True):
    """(user_agent, peer_fingerprint) for the reported qBittorrent version."""
    major, minor, patch = qbittorrent_version(allow_network=allow_network)
    # qBittorrent's own peer ID: "qB" plus its four version digits, with the
    # build slot it leaves empty -- 5.2.3 becomes -qB5230-.
    return (f"qBittorrent/{major}.{minor}.{patch}",
            f"-qB{major}{minor}{patch}0-".encode("ascii"))


def _unlimited_if_negative(value, default=0):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return 0 if value < 0 else value


def _unlimited_slots(value, default=-1):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return -1 if value <= 0 else value


def _listen_port(value, default=6881):
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    if 1 <= port <= 65535:
        return port
    return default


def _listen_interfaces(value, port):
    # Bind torrent traffic to one NIC so UPnP maps the port on that NIC's
    # router instead of the first device that answers (issue #103).
    interface = str(value or "").strip()
    if not interface:
        return f"0.0.0.0:{port},[::]:{port}"

    # The preference is explicitly a local IP address, not a hostname. Validate
    # it before handing it to libtorrent so a typo cannot break all incoming
    # binds. Accept bracketed IPv6 copied from URLs, then emit libtorrent's
    # required [address]:port form for IPv6 listeners.
    candidate = interface[1:-1] if interface.startswith("[") and interface.endswith("]") else interface
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return f"0.0.0.0:{port},[::]:{port}"

    if address.version == 6:
        return f"[{address}]:{port}"
    return f"{address}:{port}"


def _flush_resume_flag():
    # save_info_dict keeps the metadata in the .resume file; without it a
    # restarted torrent has to refetch it from peers, and one with no peers
    # sits at 0% forever while force_recheck silently does nothing (#83).
    flags = getattr(lt, "save_resume_flags_t", None) or lt.resume_data_flags_t
    return flags.flush_disk_cache | getattr(flags, "save_info_dict", 0)


def _write_resume_data_bytes(params):
    if hasattr(lt, "write_resume_data_buf"):
        return lt.write_resume_data_buf(params)
    data = lt.write_resume_data(params)
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    return lt.bencode(data)


def _handle_has_metadata(handle):
    """Whether a handle has metadata, across libtorrent versions.

    libtorrent 2.1 removed ``torrent_handle.has_metadata()``; the fact now
    lives on ``torrent_status.has_metadata``.
    """
    try:
        return bool(handle.has_metadata())
    except AttributeError:
        pass
    try:
        return bool(handle.status().has_metadata)
    except Exception:
        return False


def _start_paused_flags():
    """add_torrent_params flags for a torrent that must start paused.

    Clears ``auto_managed`` from the defaults (keeping the add-time paused
    bit) so the session's queue manager cannot start it -- the same way a
    manual pause clears auto-management. Returns None when the running
    libtorrent exposes no ``torrent_flags``.
    """
    try:
        return int(lt.torrent_flags.default_flags) & ~int(lt.torrent_flags.auto_managed)
    except Exception:
        return None


class _SessionRates:
    """Minimal stand-in for libtorrent 2.0's session_status object."""

    __slots__ = ("payload_download_rate", "payload_upload_rate")

    def __init__(self, payload_download_rate, payload_upload_rate):
        self.payload_download_rate = payload_download_rate
        self.payload_upload_rate = payload_upload_rate


class SessionManager:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SessionManager()
        return cls._instance

    def __init__(self):
        if not lt:
            raise RuntimeError("libtorrent not available")
        
        self.lock = threading.RLock()
            
        self.state_dir = get_state_dir()
        self.torrents_db_path = os.path.join(self.state_dir, 'torrents.json')
        with self.lock:
            self.torrents_db = self._load_torrents_db()

        # Create Session
        self.ses = lt.session()
        
        # Load preferences
        cm = ConfigManager()
        prefs = cm.get_preferences()
        self.auto_start_default = bool(prefs.get('auto_start', True))
        self.apply_preferences(prefs)
        
        self.alerts_queue = []
        self.running = True
        self.pending_saves = set()  # Track info_hashes for pending resume data
        self.failed_resume_saves = set()
        self.last_autosave = time.time()
        self.alert_thread = threading.Thread(target=self._alert_loop, daemon=True)
        self.alert_thread.start()
        
        with self.lock:
            self.load_state()

    def _load_torrents_db(self):
        if os.path.exists(self.torrents_db_path):
            try:
                raw = _read_bounded_state_file(
                    self.torrents_db_path,
                    TORRENTS_DB_MAX_BYTES,
                    "torrents.json",
                )
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("torrents.json root must be an object")
                return data
            except Exception as e:
                print(f"Error loading torrents.json: {e}")
                backup = self.torrents_db_path + ".corrupt"
                try:
                    shutil.copy2(self.torrents_db_path, backup)
                    if os.name != "nt":
                        try:
                            os.chmod(backup, 0o600)
                        except OSError:
                            pass
                    print(f"Preserved unreadable torrents.json as {backup}")
                except OSError as backup_error:
                    print(f"Could not preserve unreadable torrents.json: {backup_error}")
        return {}

    def _save_torrents_db(self):
        tmp = f"{self.torrents_db_path}.{os.getpid()}.tmp"
        try:
            os.makedirs(os.path.dirname(self.torrents_db_path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.torrents_db, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.torrents_db_path)
            if os.name != "nt":
                try:
                    os.chmod(self.torrents_db_path, 0o600)
                except OSError:
                    pass
            return True
        except Exception as e:
            print(f"Error saving torrents.json: {e}")
            return False
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _hash_object_key(self, value):
        if value is None:
            return ""
        try:
            if hasattr(value, "to_string"):
                raw = value.to_string()
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    raw = bytes(raw)
                    if len(raw) in (20, 32):
                        return raw.hex()
                    try:
                        return raw.decode("ascii").strip().lower()
                    except Exception:
                        return raw.decode("utf-8", "ignore").strip().lower()
        except Exception:
            pass
        try:
            text = str(value).strip()
        except Exception:
            return ""
        if text.startswith("<") and text.endswith(">"):
            return ""
        normalized = normalize_info_hash(text)
        if normalized:
            return normalized
        if text and all(c in "0123456789abcdefABCDEF" for c in text) and len(text) in (40, 64):
            return text.lower()
        return text

    def _append_hash_key(self, keys, value):
        key = self._hash_object_key(value)
        if key and key not in keys:
            keys.append(key)

    def _info_hash_keys(self, info_hashes):
        if info_hashes is None:
            return []
        keys = []
        try:
            if hasattr(info_hashes, "has_v1") and info_hashes.has_v1():
                self._append_hash_key(keys, info_hashes.v1)
            if hasattr(info_hashes, "has_v2") and info_hashes.has_v2():
                self._append_hash_key(keys, info_hashes.v2)
        except Exception:
            pass
        self._append_hash_key(keys, info_hashes)
        return keys

    def _info_hash_key(self, info_hashes):
        keys = self._info_hash_keys(info_hashes)
        return keys[0] if keys else ""

    def _info_hash_dict(self, info_hashes):
        hashes = {}
        try:
            if hasattr(info_hashes, "has_v1") and info_hashes.has_v1():
                v1 = self._hash_object_key(info_hashes.v1)
                if v1:
                    hashes["v1"] = v1
            if hasattr(info_hashes, "has_v2") and info_hashes.has_v2():
                v2 = self._hash_object_key(info_hashes.v2)
                if v2:
                    hashes["v2"] = v2
        except Exception:
            pass
        for key in self._info_hash_keys(info_hashes):
            if len(key) == 40 and "v1" not in hashes:
                hashes["v1"] = key
            elif len(key) == 64 and "v2" not in hashes:
                hashes["v2"] = key
        return hashes

    def _handle_hash_key(self, handle):
        keys = self._handle_hash_keys(handle)
        return keys[0] if keys else ""

    def _handle_hash_dict(self, handle):
        hashes = {}
        try:
            if hasattr(handle, "info_hashes"):
                hashes.update(self._info_hash_dict(handle.info_hashes()))
        except Exception:
            pass
        for key in self._handle_hash_keys(handle):
            if len(key) == 40 and "v1" not in hashes:
                hashes["v1"] = key
            elif len(key) == 64 and "v2" not in hashes:
                hashes["v2"] = key
        return hashes

    def _handle_hash_keys(self, handle):
        keys = []
        try:
            if hasattr(handle, "info_hashes"):
                keys.extend(self._info_hash_keys(handle.info_hashes()))
        except Exception:
            pass
        try:
            keys.extend(self._info_hash_keys(handle.info_hash()))
        except Exception:
            pass
        out = []
        for key in keys:
            if key and key not in out:
                out.append(key)
        return out

    def _db_entry_for_keys(self, keys):
        for key in keys:
            entry = self.torrents_db.get(key)
            if entry:
                return entry
        for entry in self.torrents_db.values():
            if not isinstance(entry, dict):
                continue
            stored_hashes = entry.get("hashes")
            if not isinstance(stored_hashes, dict):
                continue
            aliases = {self._hash_object_key(value) for value in stored_hashes.values()}
            if any(key in aliases for key in keys):
                return entry
        return None

    def _state_key_for_hash(self, info_hash):
        h = self._find_handle(info_hash)
        if h:
            key = self._handle_hash_key(h)
            if key:
                return key
        return self._hash_object_key(info_hash)

    def apply_preferences(self, prefs):
        # Proxy Mapping
        # 0=None, 1=SOCKS4, 2=SOCKS5, 3=HTTP
        p_type = prefs.get('proxy_type', 0)
        lt_proxy_type = lt.proxy_type_t.none
        
        if p_type == 1:
            lt_proxy_type = lt.proxy_type_t.socks4
        elif p_type == 2:
            lt_proxy_type = lt.proxy_type_t.socks5
            if prefs.get('proxy_user'):
                lt_proxy_type = lt.proxy_type_t.socks5_pw
        elif p_type == 3:
            lt_proxy_type = lt.proxy_type_t.http
            if prefs.get('proxy_user'):
                lt_proxy_type = lt.proxy_type_t.http_pw

        port = _listen_port(prefs.get('listen_port', 6881))
        # Cached after the first call of the day; never blocks on the network
        # once the session is up and preferences are being re-applied.
        user_agent, peer_fingerprint = qbittorrent_identity()

        settings = {
            'user_agent': user_agent,
            'peer_fingerprint': peer_fingerprint,
            'enable_dht': prefs.get('enable_dht', True),
            'enable_lsd': prefs.get('enable_lsd', True),
            'enable_upnp': prefs.get('enable_upnp', True),
            'enable_natpmp': prefs.get('enable_natpmp', True),
            'listen_interfaces': _listen_interfaces(prefs.get('listen_interface', ''), port),
            'max_retry_port_bind': 10,
            'alert_mask': lt.alert.category_t.status_notification | lt.alert.category_t.storage_notification | lt.alert.category_t.error_notification | lt.alert.category_t.port_mapping_notification,
            
            # Limits
            'connections_limit': prefs.get('max_connections', -1),
            'active_downloads': -1, # Unlimited active
            'active_seeds': -1,
            'active_limit': -1, # Total active torrents
            'unchoke_slots_limit': _unlimited_slots(prefs.get('max_uploads', -1)),
            'download_rate_limit': _unlimited_if_negative(prefs.get('dl_limit', 0)),
            'upload_rate_limit': _unlimited_if_negative(prefs.get('ul_limit', 0)),

            # Proxy
            'proxy_type': lt_proxy_type,
            'proxy_hostname': prefs.get('proxy_host', ''),
            'proxy_port': prefs.get('proxy_port', 8080),
            'proxy_username': prefs.get('proxy_user', ''),
            'proxy_password': prefs.get('proxy_password', '')
        }
        
        # IP reported to trackers (announce &ip=). Useful when inbound peer traffic
        # is forwarded from a public relay/VPS whose address differs from the local
        # egress IP. Guarded because the setting was deprecated in some libtorrent
        # builds; a failure here must not drop the core settings.
        announce_ip = str(prefs.get('announce_ip', '') or '').strip()
        if 'announce_ip' in prefs:
            settings['announce_ip'] = announce_ip
        try:
            self.ses.apply_settings(settings)
        except Exception as e:
            if 'announce_ip' not in settings:
                raise
            settings.pop('announce_ip', None)
            self.ses.apply_settings(settings)
            if announce_ip:
                print(f"announce_ip not supported by this libtorrent build: {e}")

    def _alert_loop(self):
        # Runs for the lifetime of the process, independent of which client
        # profile (local or remote) is currently selected in the UI - this is
        # the background local session and must keep ticking regardless.
        while self.running:
            try:
                if not self.ses:
                    time.sleep(0.5)
                    continue
                if self.ses.wait_for_alert(1000):
                    alerts = self.ses.pop_alerts()
                    for alert in alerts:
                        self._log_diagnostic_alert(alert)
                        if isinstance(alert, lt.save_resume_data_alert):
                            self._handle_save_resume(alert)
                        elif isinstance(alert, lt.save_resume_data_failed_alert):
                            self._handle_save_resume_failed(alert)
                        elif isinstance(alert, lt.metadata_received_alert):
                            # Persist the new metadata now so a restart does
                            # not have to fetch it from peers again.
                            try:
                                alert.handle.save_resume_data(_flush_resume_flag())
                            except Exception as e:
                                print(f"Error saving resume data after metadata: {e}")
                self._maybe_autosave()
            except Exception as e:
                print(f"Session alert loop error: {e}")
                time.sleep(1)
                continue

    def _log_diagnostic_alert(self, alert):
        kind = type(alert).__name__
        if kind not in {
            "file_error_alert", "torrent_error_alert", "fastresume_rejected_alert",
            "save_resume_data_failed_alert", "torrent_checked_alert", "state_changed_alert",
            "portmap_alert", "portmap_error_alert", "listen_failed_alert", "listen_succeeded_alert",
        }:
            return
        logger = logging.getLogger("SerrebiTorrent.session")
        if not logger.handlers:
            try:
                handler = RotatingFileHandler(get_log_path("session.log"), maxBytes=1_000_000,
                                              backupCount=2, encoding="utf-8")
                if os.name != "nt":
                    try:
                        os.chmod(handler.baseFilename, 0o600)
                    except OSError:
                        pass
            except OSError as exc:
                print(f"Could not open local session log: {exc}")
                logger.addHandler(logging.NullHandler())
                return
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        logger.info("%s: %s", kind, alert.message())
        if kind == "portmap_error_alert" and not getattr(self, "_portmap_error_hint_logged", False):
            logger.info(
                "portmap_hint: automatic router port mapping failed; local torrent listening "
                "may still work, but inbound reachability can be limited. Check the router's "
                "UPnP/NAT-PMP settings or disable the unsupported mapper."
            )
            self._portmap_error_hint_logged = True

    def _maybe_autosave(self):
        """Periodically flush resume data (ratio, totals, etc.) to disk.

        This runs on a timer independent of app shutdown so seeding stats
        survive crashes, forced restarts (e.g. auto-update), or the process
        simply never being closed gracefully - not just clean exits.
        """
        now = time.time()
        if now - self.last_autosave < AUTOSAVE_INTERVAL_SECONDS:
            return
        self.last_autosave = now
        try:
            for h in self.ses.get_torrents():
                if not h.is_valid():
                    continue
                if not _handle_has_metadata(h):
                    continue
                need_resume = getattr(h, 'need_save_resume_data', None)
                if callable(need_resume) and not need_resume():
                    continue
                ih = self._handle_hash_key(h)
                try:
                    h.save_resume_data(_flush_resume_flag())
                    if ih:
                        with self.lock:
                            self.pending_saves.add(ih)
                except Exception as e:
                    print(f"Error requesting periodic resume save for {ih}: {e}")
        except Exception as e:
            print(f"Error during periodic autosave: {e}")

    def _handle_save_resume_failed(self, alert):
        try:
            params = getattr(alert, 'params', None)
            info_hashes = getattr(params, 'info_hashes', None)
            ih = self._info_hash_key(info_hashes)
            if ih:
                self.pending_saves.discard(ih)
                self.failed_resume_saves.add(ih)
        except Exception as e:
            print(f"Error handling save_resume_data_failed_alert: {e}")

    def _handle_save_resume(self, alert):
        # alert.params is add_torrent_params
        # alert.resume_data is list of bytes (if bencoded) usually?
        # In lt 2.0, params has the resume data inside it?
        # Actually alert.params is an add_torrent_params object.
        # We can pickle it or bencode it.
        
        # Save to disk
        try:
            ih = self._info_hash_key(alert.params.info_hashes)
            if not ih:
                return
            
            path = os.path.join(self.state_dir, ih + '.resume')
            tmp = f"{path}.{os.getpid()}.tmp"
            
            data = _write_resume_data_bytes(alert.params)
            try:
                os.makedirs(self.state_dir, exist_ok=True)
                with open(tmp, 'wb') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                if os.name != "nt":
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
                self.pending_saves.discard(ih)
                self.failed_resume_saves.discard(ih)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                
            # Update DB with current save path from params if available
            # This ensures we have the latest path even if user moved it (though move is not fully implemented yet)
            if alert.params.save_path:
                 with self.lock:
                     current = self.torrents_db.get(ih)
                     current_save_path = current.get('save_path') if isinstance(current, dict) else None
                     if current_save_path != alert.params.save_path:
                          previous = dict(current) if isinstance(current, dict) else None
                          entry = dict(current) if isinstance(current, dict) else {}
                          entry['save_path'] = alert.params.save_path
                          entry.setdefault('added', time.time())
                          hashes = self._info_hash_dict(alert.params.info_hashes)
                          if hashes:
                              entry['hashes'] = hashes
                          self.torrents_db[ih] = entry
                          if not self._save_torrents_db():
                              if previous is None:
                                  self.torrents_db.pop(ih, None)
                              else:
                                  self.torrents_db[ih] = previous
                              raise OSError(f"Failed to persist resume state for {ih}.")

        except Exception as e:
            print(f"Error writing resume data: {e}")

    def add_torrent_file(self, file_content, save_path, file_priorities=None):
        info = lt.torrent_info(file_content)
        hashes = {}
        try:
            if hasattr(info, "info_hashes"):
                hashes = self._info_hash_dict(info.info_hashes())
        except Exception:
            hashes = {}
        ih = hashes.get("v1") or hashes.get("v2") or ""
        if not ih:
            ih = self._info_hash_key(info.info_hash())

        params = lt.add_torrent_params()
        params.ti = info
        params.save_path = save_path
        if file_priorities:
            params.file_priorities = list(file_priorities)
        if not self.auto_start_default:
            flags = _start_paused_flags()
            if flags is not None:
                params.flags = flags

        with self.lock:
            duplicate_keys = list(hashes.values()) or [ih]
            if any(self._find_handle(key) for key in duplicate_keys):
                raise ValueError(f"Torrent with hash {ih} already exists.")

            tpath = os.path.join(self.state_dir, ih + '.torrent')
            tmp = f"{tpath}.{os.getpid()}.tmp"
            try:
                with open(tmp, 'wb') as f:
                    f.write(file_content)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, TypeError, ValueError):
                        pass
                os.replace(tmp, tpath)
                if os.name != "nt":
                    try:
                        os.chmod(tpath, 0o600)
                    except OSError:
                        pass
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass

            try:
                added_handle = self.ses.add_torrent(params)
            except Exception:
                try:
                    if os.path.exists(tpath):
                        os.remove(tpath)
                except OSError:
                    pass
                raise

            if ih:
                entry = {'save_path': save_path, 'added': time.time()}
                if hashes:
                    entry['hashes'] = hashes
                if file_priorities:
                    entry['priorities'] = list(file_priorities)
                self.torrents_db[ih] = entry
                if not self._save_torrents_db():
                    self.torrents_db.pop(ih, None)
                    try:
                        self.ses.remove_torrent(added_handle)
                    except Exception:
                        pass
                    try:
                        if os.path.exists(tpath):
                            os.remove(tpath)
                    except OSError:
                        pass
                    raise OSError(f"Failed to persist torrent state for {ih}.")

    def update_priorities(self, info_hash, priorities):
        with self.lock:
            state_key = self._state_key_for_hash(info_hash)
            if state_key not in self.torrents_db:
                return

            entry = self.torrents_db[state_key]
            had_previous = 'priorities' in entry
            previous = list(entry.get('priorities', [])) if had_previous else None
            entry['priorities'] = list(priorities)

            if self._save_torrents_db():
                return

            if had_previous:
                entry['priorities'] = previous
            else:
                entry.pop('priorities', None)
            raise OSError(f"Failed to persist file priorities for {state_key}.")

    def add_magnet(self, url, save_path, start=None):
        params = lt.parse_magnet_uri(url)
        params.save_path = save_path
        if not (self.auto_start_default if start is None else start):
            flags = _start_paused_flags()
            if flags is not None:
                params.flags = flags
        
        # Check if already exists from magnet's hash
        hashes = self._info_hash_dict(params.info_hashes)
        ih = hashes.get("v1") or hashes.get("v2") or self._info_hash_key(params.info_hashes)
        with self.lock:
            if any(self._find_handle(key) for key in (list(hashes.values()) or [ih])):
                raise ValueError(f"Magnet with hash {ih} already exists.")

            added_handle = self.ses.add_torrent(params)

            if ih:
                 entry = {'save_path': save_path, 'added': time.time(), 'magnet_uri': url}
                 if hashes:
                     entry['hashes'] = hashes
                 self.torrents_db[ih] = entry
                 if not self._save_torrents_db():
                     self.torrents_db.pop(ih, None)
                     try:
                         self.ses.remove_torrent(added_handle)
                     except Exception:
                         pass
                     raise OSError(f"Failed to persist magnet state for {ih}.")

    @staticmethod
    def _append_missing_trackers(handle, trackers):
        current = handle.trackers()
        existing = {str(t['url']) for t in current}
        tier = max((int(t.get('tier', 0)) for t in current), default=-1) + 1
        for url in clean_tracker_urls(trackers):
            if url not in existing:
                handle.add_tracker({'url': url, 'tier': tier})
                existing.add(url)
                tier += 1

    def add_trackers(self, info_hash, trackers):
        with self.lock:
            handle = self._find_handle(info_hash)
            if not handle:
                raise LookupError("Torrent not found.")
            original = handle.trackers()
            existing = {str(t['url']) for t in original}
            new = [t for t in clean_tracker_urls(trackers) if t not in existing]
            if not new:
                return
            key = self._handle_hash_key(handle)
            entry = self._db_entry_for_keys(self._handle_hash_keys(handle))
            created = entry is None
            if created:
                entry = {'save_path': str(handle.status().save_path)}
                self.torrents_db[key] = entry
            previous = dict(entry)
            entry['extra_trackers'] = clean_tracker_urls(entry.get('extra_trackers', []) + new)
            try:
                self._append_missing_trackers(handle, new)
                if not self._save_torrents_db():
                    raise OSError("Failed to persist tracker changes.")
            except Exception:
                entry.clear()
                entry.update(previous)
                if created:
                    self.torrents_db.pop(key, None)
                handle.replace_trackers(original)
                raise

    def _restore_extra_trackers(self):
        for handle in self.ses.get_torrents():
            entry = self._db_entry_for_keys(self._handle_hash_keys(handle))
            if entry and entry.get('extra_trackers'):
                self._append_missing_trackers(handle, entry['extra_trackers'])

    def load_state(self):
        print("Loading session state...")
        loaded_hashes = set()
        default_save_path = os.path.expanduser('~') # Fallback if save_path can't be determined
        
        # 1. Scan for .resume files and try to add them
        if os.path.exists(self.state_dir):
            for f in os.listdir(self.state_dir):
                if f.endswith('.resume'):
                    try:
                        ih_from_resume = f.replace('.resume', '')
                        data = _read_bounded_state_file(
                            os.path.join(self.state_dir, f),
                            RESUME_STATE_MAX_BYTES,
                            "Resume state file",
                        )
                        params = lt.read_resume_data(data)
                        
                        ih = self._info_hash_key(params.info_hashes)
                        resume_keys = self._info_hash_keys(params.info_hashes)
                        if ih_from_resume:
                            self._append_hash_key(resume_keys, ih_from_resume)
                        
                        # Use stored save_path if available to fix corrupted/missing resume path
                        entry = self._db_entry_for_keys(resume_keys)
                        if entry:
                            stored_path = entry.get('save_path')
                            if stored_path: # and os.path.isdir(stored_path):
                                params.save_path = stored_path
                        elif not params.save_path:
                             params.save_path = default_save_path
                        # Resume files written before #83 lack the metadata;
                        # take it from the .torrent kept next to them.
                        if params.ti is None:
                            torrent_file_path = os.path.join(self.state_dir, ih_from_resume + '.torrent')
                            if os.path.exists(torrent_file_path):
                                params.ti = lt.torrent_info(torrent_file_path)

                        self.ses.add_torrent(params)
                        loaded_hashes.update(resume_keys)
                    except Exception as e:
                        print(f"Error loading resume data for {f}: {e}")
                        # If resume data fails, try to load .torrent directly if it exists.
                        ih_from_resume = f.replace('.resume', '')
                        torrent_file_path = os.path.join(self.state_dir, ih_from_resume + '.torrent')
                        if os.path.exists(torrent_file_path):
                            try:
                                torrent_content = _read_bounded_state_file(
                                    torrent_file_path,
                                    TORRENT_STATE_MAX_BYTES,
                                    "Torrent state file",
                                )
                                info = lt.torrent_info(torrent_content)
                                
                                # Fallback to .torrent
                                save_path = default_save_path
                                priorities = None
                                torrent_keys = self._info_hash_keys(info.info_hashes()) if hasattr(info, "info_hashes") else []
                                if ih_from_resume:
                                    self._append_hash_key(torrent_keys, ih_from_resume)
                                entry = self._db_entry_for_keys(torrent_keys)
                                if entry:
                                    if entry.get('save_path'):
                                        save_path = entry.get('save_path')
                                    if entry.get('priorities'):
                                        priorities = entry.get('priorities')
                                
                                params = {'ti': info, 'save_path': save_path}
                                if priorities:
                                    params['file_priorities'] = priorities
                                
                                self.ses.add_torrent(params)
                                ih = ""
                                try:
                                    if hasattr(info, "info_hashes"):
                                        ih = self._info_hash_key(info.info_hashes())
                                except Exception:
                                    ih = ""
                                if not ih:
                                    ih = self._info_hash_key(info.info_hash())
                                loaded_hashes.update(key for key in (torrent_keys or [ih]) if key)
                                print(f"Successfully loaded {ih_from_resume}.torrent after resume data failure using tracked path.")
                            except Exception as tf_e:
                                print(f"Failed to load .torrent file {torrent_file_path} as fallback: {tf_e}")

        # 2. Scan for .torrent files that were added but never had resume data saved (e.g., app crashed immediately)
        if os.path.exists(self.state_dir):
            for f in os.listdir(self.state_dir):
                if f.endswith('.torrent'):
                    ih = f.replace('.torrent', '')
                    torrent_keys = [ih] if ih else []
                    try:
                        torrent_content = _read_bounded_state_file(
                            os.path.join(self.state_dir, f),
                            TORRENT_STATE_MAX_BYTES,
                            "Torrent state file",
                        )
                        info = lt.torrent_info(torrent_content)
                        if hasattr(info, "info_hashes"):
                            for key in self._info_hash_keys(info.info_hashes()):
                                if key not in torrent_keys:
                                    torrent_keys.append(key)
                    except Exception as e:
                        print(f"Error loading torrent file {f}: {e}")
                        continue

                    if not any(key in loaded_hashes for key in torrent_keys):
                        try:
                            save_path = default_save_path
                            priorities = None
                            entry = self._db_entry_for_keys(torrent_keys)
                            if entry:
                                if entry.get('save_path'):
                                    save_path = entry.get('save_path')
                                if entry.get('priorities'):
                                    priorities = entry.get('priorities')

                            params = {'ti': info, 'save_path': save_path}
                            if priorities:
                                params['file_priorities'] = priorities
                            
                            self.ses.add_torrent(params)
                            loaded_hashes.update(key for key in torrent_keys if key)
                            print(f"Loaded {ih}.torrent from file (no resume data) using tracked path.")
                        except Exception as e:
                            print(f"Error loading torrent file {f}: {e}")

        # 3. Reload magnets whose metadata/resume data was not available before exit.
        with self.lock:
            magnet_entries = list(self.torrents_db.items())
        for db_key, entry in magnet_entries:
            if not isinstance(entry, dict):
                continue
            magnet_uri = entry.get('magnet_uri')
            if not magnet_uri:
                continue
            keys = [db_key]
            stored_hashes = entry.get('hashes')
            if isinstance(stored_hashes, dict):
                keys.extend(self._hash_object_key(value) for value in stored_hashes.values())
            if any(key in loaded_hashes for key in keys if key):
                continue
            try:
                params = lt.parse_magnet_uri(magnet_uri)
                params.save_path = entry.get('save_path') or default_save_path
                self.ses.add_torrent(params)
                loaded_hashes.update(key for key in keys if key)
                print(f"Loaded magnet {db_key} from tracked URI.")
            except Exception as e:
                print(f"Error loading magnet {db_key}: {e}")

        self._restore_extra_trackers()

    def save_state(self):
        print("Saving session state...")
        # Trigger save_resume_data for all torrents. This runs on the app's
        # shutdown/close path and blocks the UI (busy cursor) until it
        # finishes, so it must stay fast: skip flush_disk_cache here (it
        # forces a synchronous disk write flush per torrent, which is what
        # made closing/updating feel slow with several active torrents).
        # The periodic background autosave (_maybe_autosave) still flushes
        # for real crash safety without blocking anything.
        handles = self.ses.get_torrents()
        with self.lock:
            self.pending_saves.clear()
            self.failed_resume_saves.clear()

        count = 0
        for h in handles:
            if h.is_valid():
                ih = self._handle_hash_key(h)
                if not _handle_has_metadata(h):
                    continue
                need_resume = getattr(h, 'need_save_resume_data', None)
                if callable(need_resume) and not need_resume():
                    continue
                try:
                    h.save_resume_data(_flush_resume_flag())
                except Exception as e:
                    print(f"Error requesting resume data for {ih}: {e}")
                    continue
                if ih:
                    with self.lock:
                        self.pending_saves.add(ih)
                count += 1

        if count == 0:
            return

        # Actively poll for save_resume_data alerts instead of relying on background thread
        # This ensures resume data is saved even during shutdown
        start_time = time.time()
        while self.pending_saves and time.time() - start_time < 5:
            try:
                if self.ses.wait_for_alert(500):  # 500ms timeout
                    alerts = self.ses.pop_alerts()
                    for alert in alerts:
                        self._log_diagnostic_alert(alert)
                        if isinstance(alert, lt.save_resume_data_alert):
                            self._handle_save_resume(alert)
                        elif isinstance(alert, lt.save_resume_data_failed_alert):
                            self._handle_save_resume_failed(alert)
            except Exception as e:
                print(f"Error processing alerts during save: {e}")
                time.sleep(0.1)
            
        if self.pending_saves:
            print(f"Timed out waiting for {len(self.pending_saves)} resume data saves.")
        elif self.failed_resume_saves:
            print(f"Failed to save resume data for {len(self.failed_resume_saves)} torrent(s).")
        else:
            print("All resume data saved successfully.")
        
        with self.lock:
            self._save_torrents_db()

    def shutdown(self):
        """Stop alert processing and persist resume data before process exit."""
        if not self.running:
            self.save_state()
            return
        self.running = False
        if self.alert_thread.is_alive():
            self.alert_thread.join(timeout=2)
        try:
            self.ses.pause()
        except Exception as e:
            print(f"Error pausing libtorrent session during shutdown: {e}")
        self.save_state()

    def _find_handle(self, info_hash_str):
        wanted = self._hash_object_key(info_hash_str)
        if not wanted:
            return None
        for h in self.ses.get_torrents():
            if wanted in self._handle_hash_keys(h):
                return h
        return None

    def _cleanup_torrent_state(self, info_hashes):
        keys = []
        for info_hash in info_hashes:
            key = self._hash_object_key(info_hash)
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return

        with self.lock:
            expanded = list(keys)
            for db_key, entry in list(self.torrents_db.items()):
                aliases = {self._hash_object_key(db_key)}
                if isinstance(entry, dict):
                    stored_hashes = entry.get("hashes")
                    if isinstance(stored_hashes, dict):
                        aliases.update(self._hash_object_key(value) for value in stored_hashes.values())
                aliases.discard("")
                if aliases and any(key in aliases for key in keys):
                    for alias in aliases:
                        if alias not in expanded:
                            expanded.append(alias)
            keys = expanded

        for key in keys:
            for suffix in ('.torrent', '.resume'):
                path = os.path.join(self.state_dir, key + suffix)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception as e:
                    print(f"Error removing state file {path}: {e}")

        with self.lock:
            previous_db = dict(self.torrents_db)
            previous_pending = set(self.pending_saves)
            changed = False
            for key in keys:
                self.pending_saves.discard(key)
                if key in self.torrents_db:
                    del self.torrents_db[key]
                    changed = True
            for db_key, entry in list(self.torrents_db.items()):
                if not isinstance(entry, dict):
                    continue
                stored_hashes = entry.get("hashes")
                if not isinstance(stored_hashes, dict):
                    continue
                aliases = {self._hash_object_key(value) for value in stored_hashes.values()}
                if any(key in aliases for key in keys):
                    del self.torrents_db[db_key]
                    changed = True
            if changed and not self._save_torrents_db():
                self.torrents_db = previous_db
                self.pending_saves = previous_pending
                raise OSError("Failed to persist torrent removal state.")

    def remove_torrent(self, info_hash, delete_files=False):
        h = self._find_handle(info_hash)
        state_keys = [info_hash]
        if h:
            state_keys.extend(self._handle_hash_keys(h))
            flags = 0
            if delete_files:
                flags = 1
                try:
                    if hasattr(lt, 'remove_flags_t') and hasattr(lt.remove_flags_t, 'delete_files'):
                        flags = int(lt.remove_flags_t.delete_files)
                    elif hasattr(lt, 'options_t') and hasattr(lt.options_t, 'delete_files'):
                        flags = int(lt.options_t.delete_files)
                except Exception:
                    flags = 1
            self.ses.remove_torrent(h, flags)
        self._cleanup_torrent_state(state_keys)

    def get_torrents(self):
        return self.ses.get_torrents()

    def get_status(self):
        """Session-wide status with payload download/upload rates.

        libtorrent 2.1 removed ``session.status()``, so when the running
        libtorrent no longer exposes it we sum each torrent's payload rate
        instead (available on both 2.0 and 2.1). Only the rates are consumed
        (by ``LocalClient.get_global_stats``), so a small stand-in object is
        enough.
        """
        try:
            return self.ses.status()
        except AttributeError:
            pass
        down = 0
        up = 0
        for h in self.ses.get_torrents():
            try:
                if not h.is_valid():
                    continue
                st = h.status()
                down += int(st.download_payload_rate)
                up += int(st.upload_payload_rate)
            except Exception:
                continue
        return _SessionRates(down, up)
