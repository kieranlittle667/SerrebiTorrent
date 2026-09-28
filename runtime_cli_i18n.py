# Copyright (c) serrebidev and contributors
# SPDX-License-Identifier: MIT

"""Localized command-line/association and auto-start feedback for MainFrame."""

from __future__ import annotations

import os
import time

import wx

import main as legacy
from main_ui_i18n import resolved_language, tr_main


_PT_BR = {
    "Not connected to any client.": "Nenhum cliente conectado.",
    "Adding magnet link from CLI...": "Adicionando link magnet pela linha de comando...",
    "Magnet link added from CLI": "Link magnet adicionado pela linha de comando",
    "Failed to read torrent file: {error}": "Falha ao ler o arquivo torrent: {error}",
    "Adding torrent file from CLI...": "Adicionando arquivo torrent pela linha de comando...",
    "Torrent file added from CLI": "Arquivo torrent adicionado pela linha de comando",
    "Invalid argument: {arg}": "Argumento inválido: {arg}",
    "Auto-started new torrent(s)": "Novo(s) torrent(s) iniciado(s) automaticamente",
    "Auto-start failed: {error}": "Falha na inicialização automática: {error}",
    "No client connected.": "Nenhum cliente conectado.",
    "Failed to add torrent: {error}": "Falha ao adicionar torrent: {error}",
    "Failed to add magnet: {error}": "Falha ao adicionar magnet: {error}",
}


def _language(frame):
    try:
        return frame._language()
    except Exception:
        try:
            return frame.config_manager.get_preferences().get("language", "system")
        except Exception:
            return "system"


def tr_cli(text, language=None):
    translated = tr_main(text, language)
    if translated != text:
        return translated
    if resolved_language(language) == "pt-BR":
        return _PT_BR.get(text, text)
    return text


def localized_add_torrent_file_background(
    self, client, generation, data, save_path, priorities, status_msg
):
    language = _language(self)
    try:
        if generation != self.client_generation:
            return
        if not client:
            raise RuntimeError(tr_cli("No client connected.", language))
        client.add_torrent_file(data, save_path, priorities)
        wx.CallAfter(self._on_action_complete, status_msg)
    except Exception as exc:  # noqa: BLE001 - client boundary
        wx.CallAfter(
            self._on_action_error,
            tr_cli("Failed to add torrent: {error}", language).format(error=exc),
        )


def localized_add_magnet_background(self, client, generation, url, save_path, status_msg):
    original_url = url
    language = _language(self)
    try:
        if generation != self.client_generation:
            return
        if client and self._check_duplicate_magnet(client, generation, url):
            return
        trackers = self.fetch_trackers()
        if trackers:
            import urllib.parse

            for tracker in trackers:
                url += f"&tr={urllib.parse.quote(tracker)}"
        if not client:
            raise RuntimeError(tr_cli("No client connected.", language))
        if not self._submit_magnet_to_client(client, generation, original_url, url, save_path):
            return
        wx.CallAfter(self._on_action_complete, status_msg)
    except Exception as exc:  # noqa: BLE001 - client boundary
        wx.CallAfter(
            self._on_action_error,
            tr_cli("Failed to add magnet: {error}", language).format(error=exc),
        )


def localized_process_cli_arg(self, arg):
    language = _language(self)
    if not self.connected or not self.client:
        wx.LogError(tr_cli("Not connected to any client.", language))
        return

    generation = self.client_generation
    client = self.client
    if arg.lower().startswith("magnet:"):
        self._prepare_auto_start()
        hash_hint = self._maybe_hash_from_magnet(arg)
        if hash_hint:
            self.pending_hash_starts.add(hash_hint)
        self.statusbar.SetStatusText(tr_cli("Adding magnet link from CLI...", language), 0)
        self.thread_pool.submit(
            self._add_magnet_background,
            client,
            generation,
            arg,
            None,
            tr_cli("Magnet link added from CLI", language),
        )
        return

    if os.path.exists(arg):
        try:
            with open(arg, "rb") as handle:
                content = handle.read()
        except Exception as exc:  # noqa: BLE001 - filesystem boundary
            wx.LogError(
                tr_cli("Failed to read torrent file: {error}", language).format(error=exc)
            )
            return

        self._prepare_auto_start()
        hash_hint = self._maybe_hash_from_torrent_bytes(content)
        if hash_hint:
            self.pending_hash_starts.add(hash_hint)
        self.statusbar.SetStatusText(
            tr_cli("Adding torrent file from CLI...", language), 0
        )
        self.thread_pool.submit(
            self._add_torrent_file_background,
            client,
            generation,
            content,
            None,
            None,
            tr_cli("Torrent file added from CLI", language),
        )
        return

    wx.LogError(tr_cli("Invalid argument: {arg}", language).format(arg=arg))


def localized_auto_start_hashes(self, generation, hashes):
    language = _language(self)
    try:
        time.sleep(0.3)
        for torrent_hash in hashes:
            if generation != self.client_generation:
                return
            if self.client:
                self.client.start_torrent(torrent_hash)
        wx.CallAfter(
            self.statusbar.SetStatusText,
            tr_cli("Auto-started new torrent(s)", language),
            0,
        )
        wx.CallAfter(self.refresh_data)
    except Exception as exc:  # noqa: BLE001 - client boundary
        wx.CallAfter(
            self.statusbar.SetStatusText,
            tr_cli("Auto-start failed: {error}", language).format(error=exc),
            0,
        )


def install_cli_localization():
    legacy.MainFrame._add_torrent_file_background = localized_add_torrent_file_background
    legacy.MainFrame._add_magnet_background = localized_add_magnet_background
    legacy.MainFrame._process_cli_arg = localized_process_cli_arg
    legacy.MainFrame._auto_start_hashes = localized_auto_start_hashes
