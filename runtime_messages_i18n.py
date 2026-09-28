# Copyright (c) serrebidev and contributors
# SPDX-License-Identifier: MIT

"""Localized user-facing torrent action/status flows layered over main.py."""

from __future__ import annotations

import wx

import main as legacy
from main_ui_i18n import resolved_language, tr_main


_PT_BR = {
    "Enter Magnet Link or URL:": "Digite o link magnet ou a URL:",
    "Add Torrent": "Adicionar torrent",
    "Magnet Link": "Link magnet",
    "Adding magnet link...": "Adicionando link magnet...",
    "Magnet link added": "Link magnet adicionado",
    "Downloading torrent file...": "Baixando arquivo torrent...",
    "Error adding URL: {error}": "Erro ao adicionar URL: {error}",
    "Failed to download torrent from URL: {error}": "Falha ao baixar o torrent da URL: {error}",
    "Torrent download finished after the active profile changed.": "O download do torrent terminou depois que o perfil ativo foi alterado.",
    "Unknown": "Desconhecido",
    "Adding torrent...": "Adicionando torrent...",
    "Torrent added": "Torrent adicionado",
    "Connect to a client before adding torrents.": "Conecte-se a um cliente antes de adicionar torrents.",
    "{count} torrents": "{count} torrents",
    "Adding {count} torrent...": "Adicionando {count} torrent...",
    "Adding {count} torrents...": "Adicionando {count} torrents...",
    "Failed to fetch {title}: {error}": "Falha ao obter {title}: {error}",
    "Added {title}": "{title} adicionado",
    "Not connected to any client.": "Nenhum cliente conectado.",
    "No torrents selected to {action}.": "Nenhum torrent selecionado para {action}.",
    "Starting torrents...": "Iniciando torrents...",
    "Stopping torrents...": "Parando torrents...",
    "Pausing torrents...": "Pausando torrents...",
    "Resuming torrents...": "Retomando torrents...",
    "Rechecking torrents...": "Reverificando torrents...",
    "Reannouncing torrents...": "Reanunciando torrents...",
    "{action} complete": "{action} concluído",
    "{action} complete ({failed} failed). Last error: {error}": "{action} concluído ({failed} com falha). Último erro: {error}",
    "Failed to {action} torrent: {error}": "Falha ao {action} torrent: {error}",
    "Failed to {action}: {error}": "Falha ao {action}: {error}",
    "No torrents to start.": "Nenhum torrent para iniciar.",
    "No torrents to stop.": "Nenhum torrent para parar.",
    "Starting all torrents...": "Iniciando todos os torrents...",
    "Stopping all torrents...": "Parando todos os torrents...",
    "Recheck not supported by this client.": "Este cliente não oferece suporte à reverificação.",
    "Reannounce not supported by this client.": "Este cliente não oferece suporte ao novo anúncio.",
    "No torrents selected.": "Nenhum torrent selecionado.",
    "No torrent selected.": "Nenhum torrent selecionado.",
    "Info hash copied to clipboard.": "Info hash copiado para a área de transferência.",
    "Magnet link(s) copied to clipboard.": "Link(s) magnet copiado(s) para a área de transferência.",
    "Failed to access clipboard.": "Falha ao acessar a área de transferência.",
    "Opened download folder.": "Pasta de download aberta.",
    "Download folder not available.": "Pasta de download indisponível.",
}

_ACTIONS_PT_BR = {
    "start": "iniciar",
    "stop": "parar",
    "pause": "pausar",
    "resume": "retomar",
    "recheck": "reverificar",
    "reannounce": "reanunciar",
    "start all": "iniciar todos",
    "stop all": "parar todos",
}

_ACTION_DISPLAY_PT_BR = {
    "Start": "Início",
    "Stop": "Parada",
    "Pause": "Pausa",
    "Resume": "Retomada",
    "Recheck": "Reverificação",
    "Reannounce": "Novo anúncio",
    "Start all": "Início de todos",
    "Stop all": "Parada de todos",
}


def _language(frame):
    try:
        return frame._language()
    except Exception:
        try:
            return frame.config_manager.get_preferences().get("language", "system")
        except Exception:
            return "system"


def tr_runtime_message(text, language=None):
    translated = tr_main(text, language)
    if translated != text:
        return translated
    if resolved_language(language) == "pt-BR":
        return _PT_BR.get(text, text)
    return text


def _action_verb(label, language):
    if resolved_language(language) == "pt-BR":
        return _ACTIONS_PT_BR.get(str(label).lower(), str(label).lower())
    return str(label).lower()


def _action_display(label, language):
    if resolved_language(language) == "pt-BR":
        return _ACTION_DISPLAY_PT_BR.get(str(label), str(label))
    return str(label)


def _action_progress(label, language):
    if resolved_language(language) == "pt-BR":
        mapping = {
            "Start": "Starting torrents...",
            "Stop": "Stopping torrents...",
            "Pause": "Pausing torrents...",
            "Resume": "Resuming torrents...",
            "Recheck": "Rechecking torrents...",
            "Reannounce": "Reannouncing torrents...",
        }
        source = mapping.get(label)
        if source:
            return tr_runtime_message(source, language)
    return f"{label}ing torrents..."


def localized_on_search_torrents(self, event=None):
    dlg = legacy.TorrentSearchDialog(self, self.config_manager)
    try:
        if dlg.ShowModal() != wx.ID_OK or not dlg.chosen:
            return
        picked = list(dlg.chosen)
    finally:
        dlg.Destroy()

    language = _language(self)
    if not self.client:
        wx.LogError(tr_runtime_message("Connect to a client before adding torrents.", language))
        return

    name = (
        picked[0]["title"]
        if len(picked) == 1
        else tr_runtime_message("{count} torrents", language).format(count=len(picked))
    )
    adlg = legacy.AddTorrentDialog(self, name, None, self._get_default_save_path())
    try:
        if adlg.ShowModal() != wx.ID_OK:
            return
        save_path = adlg.get_selected_path() or None
    finally:
        adlg.Destroy()

    self._prepare_auto_start()
    client = self.client
    generation = self.client_generation
    source = "Adding {count} torrent..." if len(picked) == 1 else "Adding {count} torrents..."
    self.statusbar.SetStatusText(
        tr_runtime_message(source, language).format(count=len(picked)), 0
    )
    for item in picked:
        self.thread_pool.submit(
            self._add_search_result_background,
            client,
            generation,
            item,
            save_path,
        )


def localized_add_search_result_background(self, client, generation, item, save_path):
    language = _language(self)
    try:
        if generation != self.client_generation:
            return
        kind, payload = legacy.torrent_search.resolve(item)
    except Exception as exc:  # noqa: BLE001 - search provider boundary
        wx.CallAfter(
            self._on_action_error,
            tr_runtime_message("Failed to fetch {title}: {error}", language).format(
                title=item.get("title", "torrent"), error=exc
            ),
        )
        return

    completion = tr_runtime_message("Added {title}", language).format(
        title=item.get("title", "torrent")
    )
    if kind == "magnet":
        hash_hint = self._maybe_hash_from_magnet(payload)
        if hash_hint:
            self.pending_hash_starts.add(hash_hint)
        self._add_magnet_background(client, generation, payload, save_path, completion)
        return

    hash_hint = self._maybe_hash_from_torrent_bytes(payload)
    if hash_hint:
        self.pending_hash_starts.add(hash_hint)
    self._add_torrent_file_background(
        client, generation, payload, save_path, None, completion
    )


def localized_on_add_url(self, event):
    language = _language(self)
    dlg = wx.TextEntryDialog(
        self,
        tr_runtime_message("Enter Magnet Link or URL:", language),
        tr_runtime_message("Add Torrent", language),
        value=self._clipboard_torrent_value(),
    )
    try:
        if dlg.ShowModal() != wx.ID_OK:
            return
        url = dlg.GetValue().strip()
        if not self.client:
            self.statusbar.SetStatusText(
                tr_runtime_message("Not connected to any client.", language), 0
            )
            return
        try:
            default_path = self._get_default_save_path()
            if url.lower().startswith("magnet:"):
                self._queue_magnet(url)
            elif url.lower().startswith(("http://", "https://")):
                client = self.client
                generation = self.client_generation
                self.statusbar.SetStatusText(
                    tr_runtime_message("Downloading torrent file...", language), 0
                )
                self.thread_pool.submit(
                    self._download_and_add_torrent,
                    url,
                    default_path,
                    client,
                    generation,
                )
        except Exception as exc:  # noqa: BLE001 - UI boundary
            wx.LogError(
                tr_runtime_message("Error adding URL: {error}", language).format(error=exc)
            )
    finally:
        dlg.Destroy()


def localized_download_and_add_torrent(self, url, default_path, client=None, generation=None):
    try:
        data = legacy.download_torrent_url(url)
        wx.CallAfter(
            self._show_add_after_download,
            data,
            default_path,
            client,
            generation,
        )
    except Exception as exc:  # noqa: BLE001 - network boundary
        language = _language(self)
        wx.CallAfter(
            wx.LogError,
            tr_runtime_message(
                "Failed to download torrent from URL: {error}", language
            ).format(error=exc),
        )


def localized_show_add_after_download(self, data, default_path, client=None, generation=None):
    language = _language(self)
    if generation is not None and generation != self.client_generation:
        wx.LogError(
            tr_runtime_message(
                "Torrent download finished after the active profile changed.", language
            )
        )
        return
    if client is None:
        client = self.client
        generation = self.client_generation

    file_list = []
    name = tr_runtime_message("Unknown", language)
    if legacy.lt:
        try:
            info = legacy.lt.torrent_info(data)
            name = info.name()
            num = info.num_files()
            fs = legacy.torrent_file_storage(info)
            file_list = [(fs.file_path(i), fs.file_size(i)) for i in range(num)]
        except Exception:
            pass

    adlg = legacy.AddTorrentDialog(self, name, file_list, default_path)
    try:
        if adlg.ShowModal() != wx.ID_OK:
            return
        save_path = adlg.get_selected_path() or None
        priorities = adlg.get_file_priorities()
        hash_hint = self._maybe_hash_from_torrent_bytes(data)
        if not client or generation != self.client_generation:
            wx.LogError(tr_runtime_message("Not connected to any client.", language))
            return
        self._prepare_auto_start()
        if hash_hint:
            self.pending_hash_starts.add(hash_hint)
        self.statusbar.SetStatusText(
            tr_runtime_message("Adding torrent...", language), 0
        )
        self.thread_pool.submit(
            self._add_torrent_file_background,
            client,
            generation,
            data,
            save_path,
            priorities,
            tr_runtime_message("Torrent added", language),
        )
    finally:
        adlg.Destroy()


def localized_apply_to_selected(self, action, label):
    language = _language(self)
    if not self.client or not action:
        if hasattr(self, "statusbar"):
            self.statusbar.SetStatusText(
                tr_runtime_message("Not connected to any client.", language), 0
            )
        return

    hashes = legacy.action_torrent_hashes(self.torrent_list)
    if not hashes:
        message = tr_runtime_message(
            "No torrents selected to {action}.", language
        ).format(action=_action_verb(label, language))
        if hasattr(self, "statusbar"):
            self.statusbar.SetStatusText(message, 0)
        else:
            print(message)
        return

    self.statusbar.SetStatusText(_action_progress(label, language), 0)
    self.thread_pool.submit(self._apply_background, action, hashes, label)


def localized_apply_background(self, action, hashes, label):
    language = _language(self)
    failed = 0
    last_error = None
    for torrent_hash in hashes:
        try:
            action(torrent_hash)
        except Exception as exc:  # noqa: BLE001 - remote action boundary
            failed += 1
            last_error = exc

    display = _action_display(label, language)
    if failed == 0:
        wx.CallAfter(
            self._on_action_complete,
            tr_runtime_message("{action} complete", language).format(action=display),
        )
    elif failed < len(hashes):
        wx.CallAfter(
            self.statusbar.SetStatusText,
            tr_runtime_message(
                "{action} complete ({failed} failed). Last error: {error}", language
            ).format(action=display, failed=failed, error=last_error),
            0,
        )
        wx.CallAfter(self.refresh_data)
    else:
        wx.CallAfter(
            self._on_action_error,
            tr_runtime_message("Failed to {action} torrent: {error}", language).format(
                action=_action_verb(label, language), error=last_error
            ),
        )


def localized_apply_background_bulk(self, action, hashes, label):
    language = _language(self)
    failed = 0
    last_error = None
    try:
        for torrent_hash in hashes:
            try:
                action(torrent_hash)
            except Exception as exc:  # noqa: BLE001 - remote action boundary
                failed += 1
                last_error = exc
        display = _action_display(label, language)
        if failed == 0:
            wx.CallAfter(
                self._on_action_complete,
                tr_runtime_message("{action} complete", language).format(action=display),
            )
        else:
            wx.CallAfter(
                self.statusbar.SetStatusText,
                tr_runtime_message(
                    "{action} complete ({failed} failed). Last error: {error}", language
                ).format(action=display, failed=failed, error=last_error),
                0,
            )
            wx.CallAfter(self.refresh_data)
    except Exception as exc:  # noqa: BLE001 - remote action boundary
        wx.CallAfter(
            self._on_action_error,
            tr_runtime_message("Failed to {action}: {error}", language).format(
                action=_action_verb(label, language), error=exc
            ),
        )


def localized_start_all_torrents(self):
    language = _language(self)
    if not self.client or not hasattr(self.client, "start_torrent"):
        if hasattr(self, "statusbar"):
            self.statusbar.SetStatusText(
                tr_runtime_message("Not connected to any client.", language), 0
            )
        return
    hashes = self._get_all_hashes()
    if not hashes:
        self.statusbar.SetStatusText(
            tr_runtime_message("No torrents to start.", language), 0
        )
        return
    self.statusbar.SetStatusText(
        tr_runtime_message("Starting all torrents...", language), 0
    )
    self.thread_pool.submit(
        self._apply_background_bulk, self.client.start_torrent, hashes, "Start all"
    )


def localized_stop_all_torrents(self):
    language = _language(self)
    if not self.client or not hasattr(self.client, "stop_torrent"):
        if hasattr(self, "statusbar"):
            self.statusbar.SetStatusText(
                tr_runtime_message("Not connected to any client.", language), 0
            )
        return
    hashes = self._get_all_hashes()
    if not hashes:
        self.statusbar.SetStatusText(
            tr_runtime_message("No torrents to stop.", language), 0
        )
        return
    self.statusbar.SetStatusText(
        tr_runtime_message("Stopping all torrents...", language), 0
    )
    self.thread_pool.submit(
        self._apply_background_bulk, self.client.stop_torrent, hashes, "Stop all"
    )


def localized_on_recheck(self, event):
    language = _language(self)
    if not self.client or not hasattr(self.client, "recheck_torrent"):
        self.statusbar.SetStatusText(
            tr_runtime_message("Recheck not supported by this client.", language), 0
        )
        return
    self._apply_to_selected(self.client.recheck_torrent, "Recheck")


def localized_on_reannounce(self, event):
    language = _language(self)
    if not self.client or not hasattr(self.client, "reannounce_torrent"):
        self.statusbar.SetStatusText(
            tr_runtime_message("Reannounce not supported by this client.", language), 0
        )
        return
    self._apply_to_selected(self.client.reannounce_torrent, "Reannounce")


def localized_on_copy_info_hash(self, event):
    language = _language(self)
    objs, missing = self._get_selected_torrent_objects()
    hashes = [torrent.get("hash") for torrent in objs if torrent.get("hash")] + missing
    hashes = [torrent_hash for torrent_hash in hashes if torrent_hash]
    if not hashes:
        self.statusbar.SetStatusText(
            tr_runtime_message("No torrents selected.", language), 0
        )
        return
    if self._set_clipboard_text("\n".join(hashes)):
        message = "Info hash copied to clipboard."
    else:
        message = "Failed to access clipboard."
    self.statusbar.SetStatusText(tr_runtime_message(message, language), 0)


def localized_on_copy_magnet(self, event):
    language = _language(self)
    objs, missing = self._get_selected_torrent_objects()
    magnets = [legacy.torrent_magnet_link(torrent) for torrent in objs]
    magnets.extend(
        legacy.torrent_magnet_link({"hash": torrent_hash})
        for torrent_hash in missing
        if torrent_hash
    )
    magnets = [magnet for magnet in magnets if magnet]
    if not magnets:
        self.statusbar.SetStatusText(
            tr_runtime_message("No torrents selected.", language), 0
        )
        return
    if self._set_clipboard_text("\n".join(magnets)):
        message = "Magnet link(s) copied to clipboard."
    else:
        message = "Failed to access clipboard."
    self.statusbar.SetStatusText(tr_runtime_message(message, language), 0)


def localized_on_open_download_folder(self, event):
    language = _language(self)
    objs, _missing = self._get_selected_torrent_objects()
    if not objs:
        self.statusbar.SetStatusText(
            tr_runtime_message("No torrent selected.", language), 0
        )
        return
    path = objs[0].get("save_path") or self.client_default_save_path or ""
    message = (
        "Opened download folder."
        if self._open_path(path)
        else "Download folder not available."
    )
    self.statusbar.SetStatusText(tr_runtime_message(message, language), 0)


def install_runtime_message_localization():
    legacy.MainFrame.on_search_torrents = localized_on_search_torrents
    legacy.MainFrame._add_search_result_background = localized_add_search_result_background
    legacy.MainFrame.on_add_url = localized_on_add_url
    legacy.MainFrame._download_and_add_torrent = localized_download_and_add_torrent
    legacy.MainFrame._show_add_after_download = localized_show_add_after_download
    legacy.MainFrame._apply_to_selected = localized_apply_to_selected
    legacy.MainFrame._apply_background = localized_apply_background
    legacy.MainFrame._apply_background_bulk = localized_apply_background_bulk
    legacy.MainFrame.start_all_torrents = localized_start_all_torrents
    legacy.MainFrame.stop_all_torrents = localized_stop_all_torrents
    legacy.MainFrame.on_recheck = localized_on_recheck
    legacy.MainFrame.on_reannounce = localized_on_reannounce
    legacy.MainFrame.on_copy_info_hash = localized_on_copy_info_hash
    legacy.MainFrame.on_copy_magnet = localized_on_copy_magnet
    legacy.MainFrame.on_open_download_folder = localized_on_open_download_folder
