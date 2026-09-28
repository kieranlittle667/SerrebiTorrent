"""Clipboard intake and duplicate prompts shared by both desktop entry points."""

import re
from collections import deque
from urllib.parse import parse_qs, unquote, urlparse

import wx

from add_torrent_dialog import AddTorrentDialog
from i18n import translator
from torrent_parsing import clean_tracker_urls, parse_magnet_infohash


def clipboard_magnets(text):
    """Extract valid magnets without interpreting arbitrary clipboard text as URLs."""
    if not isinstance(text, str) or len(text) > 1024 * 1024:
        return []
    links = re.findall(r"magnet:\?[^\s<>\"']+", text, re.IGNORECASE)
    return list(dict.fromkeys(link for link in links if parse_magnet_infohash(link)))


def magnet_trackers(url):
    return clean_tracker_urls(parse_qs(urlparse(url).query).get("tr", []))


def clipboard_torrent_url(text):
    """Return the first recognizable torrent link without fetching any URLs."""
    if not isinstance(text, str) or len(text) > 1024 * 1024:
        return ""
    for link in re.findall(r"(?:magnet:\?|https?://)[^\s<>\"']+", text, re.IGNORECASE):
        if parse_magnet_infohash(link):
            return link
        try:
            parsed = urlparse(link)
            if (parsed.scheme.lower() in ("http", "https") and parsed.hostname
                    and parsed.port != 0 and not parsed.username and not parsed.password
                    and unquote(parsed.path).lower().endswith(".torrent")):
                return link
        except ValueError:
            continue
    return ""


class MagnetIntakeMixin:
    def _init_magnet_intake(self):
        self._clipboard_last_text = None
        self._magnet_queue = deque()
        self._magnet_busy = False
        self.clipboard_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_clipboard_timer, self.clipboard_timer)
        self.clipboard_timer.Start(1000)

    def _magnet_text(self, text):
        return translator(self.config_manager.get_preferences().get("language", "system"))(text)

    def _read_clipboard_text(self):
        if not wx.TheClipboard.Open():
            return None
        try:
            data = wx.TextDataObject()
            return data.GetText() if wx.TheClipboard.GetData(data) else ""
        finally:
            wx.TheClipboard.Close()

    def _clipboard_torrent_value(self):
        if not self.config_manager.get_preferences().get("clipboard_prefill", True):
            return ""
        try:
            return clipboard_torrent_url(self._read_clipboard_text())
        except Exception:  # noqa: BLE001 - clipboard may be locked by another app
            return ""

    def _submit_magnet_to_client(self, client, generation, original_url, url, path):
        if self._closing or generation != self.client_generation:
            return False
        if getattr(client, "handles_magnet_start", False):
            duplicate = client.add_magnet(url, path,
                self.config_manager.get_preferences().get("auto_start", True))
            if duplicate:
                wx.CallAfter(self._prompt_duplicate_magnet, client, generation, original_url, duplicate)
                return False
        else:
            client.add_torrent_url(url, path)
        return True

    def _on_clipboard_timer(self, event):
        if self._closing or not self.connected or not self.client:
            return
        if not self.config_manager.get_preferences().get("clipboard_auto_add", False):
            return
        # Modal dialogs run a nested event loop. Defer intake until they close.
        if not self.IsEnabled() or self._magnet_busy:
            return
        try:
            text = self._read_clipboard_text()
        except Exception:  # noqa: BLE001 - clipboard may be locked by another app
            return  # Another application may temporarily own the clipboard.
        if text is None or text == self._clipboard_last_text:
            return
        self._clipboard_last_text = text
        for url in clipboard_magnets(text):
            self._queue_magnet(url)

    def _queue_magnet(self, url):
        if not self.client or self._closing:
            return
        self._magnet_queue.append((self.client, self.client_generation, url.strip()))
        self._drain_magnet_queue()

    def _drain_magnet_queue(self):
        if self._closing or self._magnet_busy:
            return
        while self._magnet_queue:
            client, generation, url = self._magnet_queue.popleft()
            if generation != self.client_generation:
                continue
            self._magnet_busy = True
            self.thread_pool.submit(self._inspect_magnet, client, generation, url)
            break

    def _inspect_magnet(self, client, generation, url):
        try:
            duplicate = client.find_magnet_duplicate(url)
            path = None
            if not duplicate and getattr(client, "handles_magnet_start", False):
                path = client.get_default_save_path() or ""
            wx.CallAfter(self._show_magnet, client, generation, url, duplicate, None, path)
        except Exception as exc:  # noqa: BLE001 - remote client boundary
            wx.CallAfter(self._show_magnet, client, generation, url, None, str(exc))

    def _show_magnet(self, client, generation, url, duplicate, error, default_path=None):
        if not self._closing and generation == self.client_generation and not self.IsEnabled():
            wx.CallLater(200, self._show_magnet, client, generation, url, duplicate, error, default_path)
            return
        try:
            if self._closing or generation != self.client_generation:
                return
            if error:
                self._on_action_error(error)
                return
            self.Show()
            if self.IsIconized():
                self.Iconize(False)
            self.Raise()
            if duplicate:
                self._prompt_duplicate_magnet(client, generation, url, duplicate)
                return
            name = parse_qs(urlparse(url).query).get("dn", [self._magnet_text("Magnet Link")])[0]
            path = self._get_default_save_path() if default_path is None else default_path
            dlg = AddTorrentDialog(self, name, None, path)
            try:
                if dlg.ShowModal() != wx.ID_OK or generation != self.client_generation:
                    return
                path = dlg.get_selected_path() or None
            finally:
                dlg.Destroy()
            # Backends handle start policy themselves; do not schedule a start
            # by hash that could accidentally restart a duplicate paused torrent.
            if not getattr(client, "handles_magnet_start", False):
                self._prepare_auto_start()
            self.thread_pool.submit(self._add_magnet_background, client, generation,
                                    url, path, self._magnet_text("Magnet link added"))
        finally:
            self._magnet_busy = False
            wx.CallAfter(self._drain_magnet_queue)

    def _check_duplicate_magnet(self, client, generation, url):
        duplicate = client.find_magnet_duplicate(url)
        if not duplicate:
            return False
        wx.CallAfter(self._prompt_duplicate_magnet, client, generation, url, duplicate)
        return True

    def _prompt_duplicate_magnet(self, client, generation, url, duplicate):
        if self._closing or generation != self.client_generation:
            return
        if not self.IsEnabled():
            wx.CallLater(200, self._prompt_duplicate_magnet, client, generation, url, duplicate)
            return
        info_hash, name, existing = duplicate
        self.pending_hash_starts.discard(info_hash)
        incoming = [t for t in magnet_trackers(url) if t not in existing]
        if not incoming:
            wx.MessageBox(self._magnet_text("This torrent is already in the list. The link contains no new trackers."),
                          self._magnet_text("Torrent already added"), wx.OK | wx.ICON_INFORMATION, self)
            return
        message = self._magnet_text('"{name}" is already in the list. Add {count} new tracker(s) from this link?').format(name=name, count=len(incoming))
        if wx.MessageBox(message, self._magnet_text("Torrent already added"),
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES:
            self.thread_pool.submit(self._merge_magnet_trackers, client, generation, info_hash, incoming)

    def _merge_magnet_trackers(self, client, generation, info_hash, trackers):
        if self._closing or generation != self.client_generation:
            return
        try:
            client.add_trackers(info_hash, trackers)
            if generation == self.client_generation and not self._closing:
                wx.CallAfter(self._on_action_complete, self._magnet_text("Trackers added to existing torrent"))
        except Exception as exc:  # noqa: BLE001 - remote client boundary
            if generation == self.client_generation and not self._closing:
                wx.CallAfter(self._on_action_error, str(exc))
