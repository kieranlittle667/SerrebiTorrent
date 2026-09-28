from unittest.mock import Mock

import pytest
from transmission_rpc import Torrent

from clients import TransmissionClient


@pytest.mark.parametrize('selected,left,downloaded,expected', [
    (1000, 0, 0, 1000),  # Existing files verified without downloading.
    (1000, 750, 9000, 250),  # Lifetime traffic is not current completion.
    (400, 100, 300, 300),  # Only selected files count toward completion.
    (400, 0, 300, 400),
    (0, 0, 1000, 0),  # Metadata not available yet / no wanted data.
    (400, 500, 0, 0),  # Defensive bounds for inconsistent RPC snapshots.
    (400, -10, 0, 400),
])
def test_progress_uses_wanted_bytes_remaining(selected, left, downloaded, expected):
    client = object.__new__(TransmissionClient)
    client.c = Mock()
    client.c.get_torrents.return_value = [Torrent(fields={
        'id': 1, 'hashString': 'a' * 40, 'name': 'Test', 'status': 4,
        'totalSize': 1000, 'sizeWhenDone': selected, 'leftUntilDone': left,
        'downloadedEver': downloaded,
    })]
    row = client.get_torrents_full()[0]
    assert row['size'] == selected
    assert row['done'] == expected


def test_completion_accepts_legacy_dictionary_field_names():
    client = object.__new__(TransmissionClient)
    assert client._completion_bytes({'sizeWhenDone': 300, 'leftUntilDone': 50}) == (300, 250)


def test_missing_remaining_bytes_falls_back_to_present_data_not_network_traffic():
    client = object.__new__(TransmissionClient)
    assert client._completion_bytes({
        'totalSize': 1000, 'haveValid': 600, 'haveUnchecked': 100, 'downloadedEver': 5000,
    }) == (1000, 700)
