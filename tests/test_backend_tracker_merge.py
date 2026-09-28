import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest

from clients import LocalClient, QBittorrentClient, RTorrentClient, TransmissionClient
from session_manager import SessionManager

HASH = 'a' * 40
MAGNET = f'magnet:?xt=urn:btih:{HASH}'
OLD = 'https://old.example/announce'
NEW = 'https://new.example/announce?token=keep%2Bthis'


def qbittorrent():
    client = object.__new__(QBittorrentClient)
    client.c = Mock()
    client.c.torrents_info.return_value = [SimpleNamespace(hash=HASH.upper(), name='Existing')]
    client.c.torrents_trackers.return_value = [{'url': OLD}]
    return client


def rtorrent():
    client = object.__new__(RTorrentClient)
    client.srv = Mock()
    client.srv.d.multicall2.return_value = [(HASH.upper(), 'Existing')]
    client.srv.t.multicall.return_value = [(OLD, 1, 0)]
    return client


def local():
    client = object.__new__(LocalClient)
    client.m = Mock()
    handle = client.m._find_handle.return_value
    handle.status.return_value = SimpleNamespace(name='Existing', save_path='/unchanged')
    handle.trackers.return_value = [{'url': OLD, 'tier': 0}]
    return client


@pytest.mark.parametrize('factory', [qbittorrent, rtorrent, local])
def test_duplicate_lookup_and_add_do_not_restart_or_readd(factory):
    client = factory()
    duplicate = client.find_magnet_duplicate(MAGNET)
    assert duplicate[0].lower() == HASH
    assert duplicate[1:] == ('Existing', [OLD])
    client._add_new_magnet = Mock()
    assert client.add_magnet(MAGNET, '/different', True) == duplicate
    client._add_new_magnet.assert_not_called()


def test_qbittorrent_only_adds_missing_trackers():
    client = qbittorrent()
    client.add_trackers(HASH, [OLD, NEW, NEW])
    client.c.torrents_add_trackers.assert_called_once_with(torrent_hash=HASH, urls=[NEW])
    client.c.torrents_add.assert_not_called()
    client.c.torrents_start.assert_not_called()
    client.c.torrents_set_location.assert_not_called()


def test_qbittorrent_v2_uses_truncated_backend_id():
    client = qbittorrent()
    full_hash = 'b' * 64
    client.c.torrents_info.return_value = [SimpleNamespace(hash=full_hash[:40], name='V2')]
    result = client.find_magnet_duplicate('magnet:?xt=urn:btmh:1220' + full_hash)
    assert result[0] == full_hash[:40]
    assert client.c.torrents_info.call_args.kwargs['torrent_hashes'] == [full_hash, full_hash[:40]]


@pytest.mark.parametrize('start', [True, False])
def test_new_qbittorrent_magnet_uses_native_start_option(start):
    client = qbittorrent()
    client.c.torrents_info.return_value = []
    assert client.add_magnet(MAGNET, '/chosen', start) is None
    client.c.torrents_add.assert_called_once_with(urls=MAGNET, save_path='/chosen', is_paused=not start)


def test_rtorrent_preserves_existing_groups_and_saves_session():
    client = rtorrent()
    client.srv.t.multicall.return_value = [(OLD, 4)]
    client.add_trackers(HASH, [OLD, NEW, NEW])
    client.srv.d.tracker.insert.assert_called_once_with(HASH, 5, NEW)
    client.srv.d.save_full_session.assert_called_once_with(HASH)
    client.srv.d.start.assert_not_called()
    client.srv.d.stop.assert_not_called()
    client.srv.load.start.assert_not_called()


def test_rtorrent_does_not_exceed_tracker_group_limit():
    client = rtorrent()
    client.srv.t.multicall.return_value = [(OLD, 32)]
    client.add_trackers(HASH, [NEW, 'https://second.example/announce'])
    assert [c.args[1] for c in client.srv.d.tracker.insert.call_args_list] == [32, 32]


@pytest.mark.parametrize('start', [True, False])
def test_rtorrent_new_magnet_uses_correct_load_method(start):
    client = rtorrent()
    client.srv.d.multicall2.return_value = []
    client.add_magnet(MAGNET, '/chosen', start)
    method = client.srv.load.start if start else client.srv.load.normal
    method.assert_called_once_with('', MAGNET, 'd.directory.set=/chosen')


def test_local_add_delegates_start_and_tracker_persistence_to_manager():
    client = local()
    client.m._find_handle.return_value = None
    client.add_magnet(MAGNET, '/chosen', False)
    client.m.add_magnet.assert_called_once_with(MAGNET, '/chosen', start=False)
    client.add_trackers(HASH, [NEW])
    client.m.add_trackers.assert_called_once_with(HASH, [NEW])


@pytest.mark.parametrize('client_type', [TransmissionClient, QBittorrentClient, RTorrentClient, LocalClient])
def test_all_backends_recheck_duplicates_inside_add_guard(client_type):
    client = object.__new__(client_type)
    client.find_magnet_duplicate = Mock(return_value=(HASH, 'Existing', [OLD]))
    client._add_new_magnet = Mock()
    assert client.add_magnet(MAGNET, '/changed', False)
    client._add_new_magnet.assert_not_called()


@pytest.mark.parametrize('factory', [qbittorrent, rtorrent])
def test_lookup_errors_propagate_without_adding(factory):
    client = factory()
    client.find_magnet_duplicate = Mock(side_effect=RuntimeError('offline'))
    client._add_new_magnet = Mock()
    with pytest.raises(RuntimeError, match='offline'):
        client.add_magnet(MAGNET, '/chosen', True)
    client._add_new_magnet.assert_not_called()


def manager_with_handle():
    manager = object.__new__(SessionManager)
    manager.lock = threading.RLock()
    manager.torrents_db = {HASH: {'save_path': '/unchanged', 'magnet_uri': MAGNET}}
    handle = Mock()
    handle.trackers.return_value = [{'url': OLD, 'tier': 2}]
    manager._find_handle = Mock(return_value=handle)
    manager._handle_hash_key = Mock(return_value=HASH)
    manager._handle_hash_keys = Mock(return_value=[HASH])
    manager._save_torrents_db = Mock(return_value=True)
    manager.ses = Mock()
    manager.ses.get_torrents.return_value = [handle]
    return manager, handle


def test_local_merge_persists_and_restores_trackers_without_metadata():
    manager, handle = manager_with_handle()
    manager.add_trackers(HASH, [OLD, NEW, NEW])
    handle.add_tracker.assert_called_once_with({'url': NEW, 'tier': 3})
    assert manager.torrents_db[HASH]['extra_trackers'] == [NEW]
    assert manager.torrents_db[HASH]['save_path'] == '/unchanged'
    handle.resume.assert_not_called()
    handle.pause.assert_not_called()
    handle.add_tracker.reset_mock()
    manager._restore_extra_trackers()
    handle.add_tracker.assert_called_once_with({'url': NEW, 'tier': 3})


def test_local_merge_rolls_back_if_persistence_fails():
    manager, handle = manager_with_handle()
    original = dict(manager.torrents_db[HASH])
    manager._save_torrents_db.return_value = False
    with pytest.raises(OSError, match='persist tracker'):
        manager.add_trackers(HASH, [NEW])
    assert manager.torrents_db[HASH] == original
    handle.replace_trackers.assert_called_once_with([{'url': OLD, 'tier': 2}])


def test_local_merge_refuses_removed_torrent():
    manager, _ = manager_with_handle()
    manager._find_handle.return_value = None
    with pytest.raises(LookupError):
        manager.add_trackers(HASH, [NEW])
    manager._save_torrents_db.assert_not_called()


def test_native_local_merge_keeps_paused_state_and_restores_saved_trackers(tmp_path):
    from libtorrent_env import prepare_libtorrent_dlls
    prepare_libtorrent_dlls()
    lt = pytest.importorskip('libtorrent')
    manager = object.__new__(SessionManager)
    manager.lock = threading.RLock()
    manager.torrents_db = {}
    manager.torrents_db_path = str(tmp_path / 'torrents.json')
    manager.auto_start_default = False
    manager.ses = lt.session({
        'listen_interfaces': '127.0.0.1:0', 'enable_dht': False,
        'enable_lsd': False, 'enable_upnp': False, 'enable_natpmp': False,
    })
    manager.ses.pause()
    client = object.__new__(LocalClient)
    client.m = manager
    url = MAGNET + '&tr=' + quote(OLD, safe='')
    client.add_magnet(url, str(tmp_path), False)
    handle = manager._find_handle(HASH)
    before = handle.status()
    assert before.paused
    assert client.add_magnet(url, str(tmp_path / 'different'), True)[0] == HASH
    client.add_trackers(HASH, [OLD, NEW, NEW])
    assert [t['url'] for t in handle.trackers()].count(NEW) == 1
    assert handle.status().paused
    assert handle.status().save_path == before.save_path
    assert len(manager.ses.get_torrents()) == 1
    saved = json.loads((tmp_path / 'torrents.json').read_text())
    assert saved[HASH]['extra_trackers'] == [NEW]
    # Replay persisted extras on a handle that only has the original trackers.
    handle.replace_trackers([{'url': OLD, 'tier': 0}])
    manager.torrents_db = saved
    manager._restore_extra_trackers()
    assert {t['url'] for t in handle.trackers()} == {OLD, NEW}
    assert handle.status().paused
