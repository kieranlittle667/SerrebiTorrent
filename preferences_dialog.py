# Copyright (c) serrebidev and contributors
# SPDX-License-Identifier: MIT

"""Localized local-session preferences dialog.

This module is intentionally separate from ``main.py`` so settings can evolve
without making the already-large main frame harder to maintain.  Preference
keys and stored values remain unchanged; only presentation is localized.
"""

from __future__ import annotations

import wx

from i18n import language_options, translator
from rss_manager import RSSManager


def _language_index(options, value):
    values = [item[0] for item in options]
    try:
        return values.index(value)
    except ValueError:
        # A catalog can disappear or be renamed in a later release. Match the
        # runtime's unsupported-language fallback instead of claiming that the
        # saved preference was "system".
        try:
            return values.index("en")
        except ValueError:
            return 0


def _language_value(options, index):
    if 0 <= index < len(options):
        return options[index][0]
    return "system"


class PreferencesDialog(wx.Dialog):
    def __init__(self, parent, config_manager):
        self.cm = config_manager
        self.prefs = self.cm.get_preferences()
        self._ = translator(self.prefs.get("language", "system"))
        super().__init__(parent, title=self._("Local Session Settings"), size=(560, 560))

        sizer = wx.BoxSizer(wx.VERTICAL)
        notebook = wx.Notebook(self)

        # --- General Tab ---
        general_panel = wx.Panel(notebook)
        gen_sizer = wx.BoxSizer(wx.VERTICAL)

        language_row = wx.BoxSizer(wx.HORIZONTAL)
        language_row.Add(
            wx.StaticText(general_panel, label=self._("Language:")),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self._language_options = language_options(self.prefs.get("language", "system"))
        self.language_choice = wx.Choice(
            general_panel,
            choices=[label for _value, label in self._language_options],
        )
        self.language_choice.SetName(self._("Language:"))
        self.language_choice.SetSelection(
            _language_index(self._language_options, self.prefs.get("language", "system"))
        )
        language_row.Add(self.language_choice, 1, wx.EXPAND)
        gen_sizer.Add(language_row, 0, wx.EXPAND | wx.ALL, 5)

        gen_sizer.Add(
            wx.StaticText(general_panel, label=self._("Default Download Path:")),
            0,
            wx.ALL,
            5,
        )
        path_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.path_input = wx.TextCtrl(general_panel, value=self.prefs.get("download_path", ""))
        self.path_input.SetName(self._("Default Download Path:"))
        path_sizer.Add(self.path_input, 1, wx.EXPAND | wx.RIGHT, 5)
        browse_btn = wx.Button(general_panel, label=self._("Browse..."))
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        path_sizer.Add(browse_btn, 0)
        gen_sizer.Add(path_sizer, 0, wx.EXPAND | wx.ALL, 5)

        gen_sizer.Add(
            wx.StaticText(
                general_panel,
                label=self._("Watch folder (checked every minute; leave empty to turn off):"),
            ),
            0,
            wx.ALL,
            5,
        )
        watch_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.watch_input = wx.TextCtrl(general_panel, value=self.prefs.get("watch_folder", ""))
        self.watch_input.SetName(self._("Watch folder (checked every minute; leave empty to turn off):"))
        watch_sizer.Add(self.watch_input, 1, wx.EXPAND | wx.RIGHT, 5)
        watch_btn = wx.Button(general_panel, label=self._("Browse..."))
        watch_btn.Bind(wx.EVT_BUTTON, self.on_browse_watch)
        watch_sizer.Add(watch_btn, 0)
        gen_sizer.Add(watch_sizer, 0, wx.EXPAND | wx.ALL, 5)

        self.auto_start_chk = wx.CheckBox(
            general_panel, label=self._("Automatically start torrents"))
        self.auto_start_chk.SetValue(self.prefs.get("auto_start", True))
        gen_sizer.Add(self.auto_start_chk, 0, wx.ALL, 5)

        self.clipboard_chk = wx.CheckBox(general_panel, label=self._("Automatically open the Add Torrent dialog for clipboard magnets"))
        self.clipboard_chk.SetValue(self.prefs.get("clipboard_auto_add", False))
        gen_sizer.Add(self.clipboard_chk, 0, wx.ALL, 5)

        self.clipboard_prefill_chk = wx.CheckBox(general_panel, label=self._("Prefill Add URL from clipboard (magnets and .torrent URLs)"))
        self.clipboard_prefill_chk.SetValue(self.prefs.get("clipboard_prefill", True))
        gen_sizer.Add(self.clipboard_prefill_chk, 0, wx.ALL, 5)

        self.min_tray_chk = wx.CheckBox(
            general_panel, label=self._("Minimize to System Tray"))
        self.min_tray_chk.SetValue(self.prefs.get("min_to_tray", True))
        gen_sizer.Add(self.min_tray_chk, 0, wx.ALL, 5)

        self.close_tray_chk = wx.CheckBox(
            general_panel, label=self._("Close to System Tray"))
        self.close_tray_chk.SetValue(self.prefs.get("close_to_tray", True))
        gen_sizer.Add(self.close_tray_chk, 0, wx.ALL, 5)

        self.auto_update_chk = wx.CheckBox(
            general_panel,
            label=self._("Check for updates automatically on startup"),
        )
        self.auto_update_chk.SetValue(self.prefs.get("auto_check_updates", True))
        gen_sizer.Add(self.auto_update_chk, 0, wx.ALL, 5)

        general_panel.SetSizer(gen_sizer)
        notebook.AddPage(general_panel, self._("General"))

        # --- Connection Tab ---
        conn_panel = wx.Panel(notebook)
        conn_sizer = wx.BoxSizer(wx.VERTICAL)
        conn_sizer.Add(
            wx.StaticText(
                conn_panel,
                label=self._("Global Limits (0 or -1 for unlimited):"),
            ),
            0,
            wx.ALL,
            5,
        )

        grid = wx.FlexGridSizer(4, 2, 10, 10)
        grid.Add(wx.StaticText(conn_panel, label=self._("Download Rate (bytes/s):")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.dl_limit = wx.SpinCtrl(
            conn_panel, min=-1, max=1000000000, initial=self.prefs.get("dl_limit", 0))
        self.dl_limit.SetName(self._("Download Rate (bytes/s):"))
        grid.Add(self.dl_limit, 0, wx.EXPAND)

        grid.Add(wx.StaticText(conn_panel, label=self._("Upload Rate (bytes/s):")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.ul_limit = wx.SpinCtrl(
            conn_panel, min=-1, max=1000000000, initial=self.prefs.get("ul_limit", 0))
        self.ul_limit.SetName(self._("Upload Rate (bytes/s):"))
        grid.Add(self.ul_limit, 0, wx.EXPAND)

        grid.Add(wx.StaticText(conn_panel, label=self._("Max Connections:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.max_conn = wx.SpinCtrl(
            conn_panel, min=-1, max=65535, initial=self.prefs.get("max_connections", -1))
        self.max_conn.SetName(self._("Max Connections:"))
        grid.Add(self.max_conn, 0, wx.EXPAND)

        grid.Add(wx.StaticText(conn_panel, label=self._("Max Upload Slots:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self.max_slots = wx.SpinCtrl(
            conn_panel, min=-1, max=65535, initial=self.prefs.get("max_uploads", -1))
        self.max_slots.SetName(self._("Max Upload Slots:"))
        grid.Add(self.max_slots, 0, wx.EXPAND)
        conn_sizer.Add(grid, 0, wx.ALL, 10)

        conn_sizer.Add(wx.StaticLine(conn_panel), 0, wx.EXPAND | wx.ALL, 5)

        port_sizer = wx.BoxSizer(wx.HORIZONTAL)
        port_sizer.Add(wx.StaticText(conn_panel, label=self._("Listening Port:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.port_input = wx.SpinCtrl(
            conn_panel, min=1, max=65535, initial=self.prefs.get("listen_port", 6881))
        self.port_input.SetName(self._("Listening Port:"))
        port_sizer.Add(self.port_input, 0)
        conn_sizer.Add(port_sizer, 0, wx.ALL, 10)

        ann_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ann_sizer.Add(
            wx.StaticText(
                conn_panel,
                label=self._("Announce IP (reported to trackers, blank = auto):"),
            ),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            5,
        )
        self.announce_ip_input = wx.TextCtrl(
            conn_panel, value=self.prefs.get("announce_ip", ""))
        self.announce_ip_input.SetName(self._("Announce IP (reported to trackers, blank = auto):"))
        ann_sizer.Add(self.announce_ip_input, 1, wx.EXPAND)
        conn_sizer.Add(ann_sizer, 0, wx.EXPAND | wx.ALL, 10)

        listen_sizer = wx.BoxSizer(wx.HORIZONTAL)
        listen_sizer.Add(
            wx.StaticText(
                conn_panel,
                label=self._("Listen interface (local IP to bind, blank = all):"),
            ),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            5,
        )
        self.listen_interface_input = wx.TextCtrl(
            conn_panel, value=self.prefs.get("listen_interface", ""))
        self.listen_interface_input.SetName(self._("Listen interface (local IP to bind, blank = all):"))
        listen_sizer.Add(self.listen_interface_input, 1, wx.EXPAND)
        conn_sizer.Add(listen_sizer, 0, wx.EXPAND | wx.ALL, 10)

        self.upnp_chk = wx.CheckBox(
            conn_panel, label=self._("Enable UPnP Port Mapping"))
        self.upnp_chk.SetValue(self.prefs.get("enable_upnp", True))
        conn_sizer.Add(self.upnp_chk, 0, wx.ALL, 5)

        self.natpmp_chk = wx.CheckBox(
            conn_panel, label=self._("Enable NAT-PMP Port Mapping"))
        self.natpmp_chk.SetValue(self.prefs.get("enable_natpmp", True))
        conn_sizer.Add(self.natpmp_chk, 0, wx.ALL, 5)

        self.dht_chk = wx.CheckBox(conn_panel, label=self._("Enable DHT"))
        self.dht_chk.SetValue(self.prefs.get("enable_dht", True))
        conn_sizer.Add(self.dht_chk, 0, wx.ALL, 5)

        self.lsd_chk = wx.CheckBox(
            conn_panel, label=self._("Enable Local Service Discovery (LSD)"))
        self.lsd_chk.SetValue(self.prefs.get("enable_lsd", True))
        conn_sizer.Add(self.lsd_chk, 0, wx.ALL, 5)

        conn_panel.SetSizer(conn_sizer)
        notebook.AddPage(conn_panel, self._("Connection"))

        # --- Trackers Tab ---
        track_panel = wx.Panel(notebook)
        track_sizer = wx.BoxSizer(wx.VERTICAL)
        self.track_chk = wx.CheckBox(
            track_panel, label=self._("Automatically add trackers from URL"))
        self.track_chk.SetValue(self.prefs.get("enable_trackers", True))
        track_sizer.Add(self.track_chk, 0, wx.ALL, 5)
        track_sizer.Add(
            wx.StaticText(track_panel, label=self._("Tracker List URL:")),
            0,
            wx.ALL,
            5,
        )
        self.track_url_input = wx.TextCtrl(
            track_panel, value=self.prefs.get("tracker_url", ""))
        self.track_url_input.SetName(self._("Tracker List URL:"))
        track_sizer.Add(self.track_url_input, 0, wx.EXPAND | wx.ALL, 5)
        track_panel.SetSizer(track_sizer)
        notebook.AddPage(track_panel, self._("Trackers"))

        # --- RSS Tab ---
        rss_panel = wx.Panel(notebook)
        rss_sizer = wx.BoxSizer(wx.VERTICAL)
        rss_interval_sizer = wx.BoxSizer(wx.HORIZONTAL)
        rss_interval_sizer.Add(
            wx.StaticText(rss_panel, label=self._("RSS Update Interval (seconds):")),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            5,
        )
        self.rss_interval = wx.SpinCtrl(
            rss_panel, min=5, max=86400, initial=self.prefs.get("rss_update_interval", 300))
        self.rss_interval.SetName(self._("RSS Update Interval (seconds):"))
        rss_interval_sizer.Add(self.rss_interval, 0)
        rss_sizer.Add(rss_interval_sizer, 0, wx.ALL, 10)
        rss_sizer.Add(wx.StaticLine(rss_panel), 0, wx.EXPAND | wx.ALL, 5)
        self.reset_rss_btn = wx.Button(
            rss_panel, label=self._("Reset RSS (Clear all feeds and rules)"))
        self.reset_rss_btn.Bind(wx.EVT_BUTTON, self.on_reset_rss)
        rss_sizer.Add(self.reset_rss_btn, 0, wx.ALL, 10)
        rss_panel.SetSizer(rss_sizer)
        notebook.AddPage(rss_panel, "RSS")

        # --- Web UI Tab ---
        web_panel = wx.Panel(notebook)
        web_sizer = wx.BoxSizer(wx.VERTICAL)
        self.web_enabled_chk = wx.CheckBox(web_panel, label=self._("Enable Web UI"))
        self.web_enabled_chk.SetValue(self.prefs.get("web_ui_enabled", False))
        web_sizer.Add(self.web_enabled_chk, 0, wx.ALL, 10)
        web_grid = wx.FlexGridSizer(4, 2, 10, 10)
        web_grid.Add(wx.StaticText(web_panel, label=self._("Bind Host:")), 0,
                     wx.ALIGN_CENTER_VERTICAL)
        self.web_host = wx.TextCtrl(
            web_panel, value=self.prefs.get("web_ui_host", "127.0.0.1"))
        self.web_host.SetName(self._("Bind Host:"))
        web_grid.Add(self.web_host, 0, wx.EXPAND)
        web_grid.Add(wx.StaticText(web_panel, label=self._("Port:")), 0,
                     wx.ALIGN_CENTER_VERTICAL)
        self.web_port = wx.SpinCtrl(
            web_panel, min=1, max=65535, initial=self.prefs.get("web_ui_port", 8080))
        self.web_port.SetName(self._("Port:"))
        web_grid.Add(self.web_port, 0, wx.EXPAND)
        web_grid.Add(wx.StaticText(web_panel, label=self._("Username:")), 0,
                     wx.ALIGN_CENTER_VERTICAL)
        self.web_user = wx.TextCtrl(
            web_panel, value=self.prefs.get("web_ui_user", "admin"))
        self.web_user.SetName(self._("Username:"))
        web_grid.Add(self.web_user, 0, wx.EXPAND)
        web_grid.Add(wx.StaticText(web_panel, label=self._("Password:")), 0,
                     wx.ALIGN_CENTER_VERTICAL)
        self.web_pass = wx.TextCtrl(
            web_panel,
            value=self.prefs.get("web_ui_pass", "password"),
            style=wx.TE_PASSWORD,
        )
        self.web_pass.SetName(self._("Password:"))
        web_grid.Add(self.web_pass, 0, wx.EXPAND)
        web_sizer.Add(web_grid, 0, wx.ALL, 10)
        web_panel.SetSizer(web_sizer)
        notebook.AddPage(web_panel, "Web UI")

        # --- Proxy Tab ---
        proxy_panel = wx.Panel(notebook)
        proxy_sizer = wx.BoxSizer(wx.VERTICAL)
        proxy_sizer.Add(
            wx.StaticText(proxy_panel, label=self._("Proxy Type:")), 0, wx.ALL, 5)
        self.proxy_type = wx.Choice(
            proxy_panel, choices=[self._("None"), "SOCKS4", "SOCKS5", "HTTP"])
        self.proxy_type.SetSelection(self.prefs.get("proxy_type", 0))
        self.proxy_type.SetName(self._("Proxy Type:"))
        proxy_sizer.Add(self.proxy_type, 0, wx.EXPAND | wx.ALL, 5)

        hp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        hp_sizer.Add(wx.StaticText(proxy_panel, label=self._("Host:")), 0,
                     wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.proxy_host = wx.TextCtrl(
            proxy_panel, value=self.prefs.get("proxy_host", ""))
        self.proxy_host.SetName(self._("Host:"))
        hp_sizer.Add(self.proxy_host, 1, wx.EXPAND | wx.RIGHT, 10)
        hp_sizer.Add(wx.StaticText(proxy_panel, label=self._("Port:")), 0,
                     wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.proxy_port = wx.SpinCtrl(
            proxy_panel, min=1, max=65535, initial=self.prefs.get("proxy_port", 8080))
        self.proxy_port.SetName(self._("Port:"))
        hp_sizer.Add(self.proxy_port, 0)
        proxy_sizer.Add(hp_sizer, 0, wx.EXPAND | wx.ALL, 5)

        proxy_sizer.Add(
            wx.StaticText(proxy_panel, label=self._("Authentication (if required):")),
            0,
            wx.TOP | wx.LEFT,
            10,
        )
        user_sizer = wx.BoxSizer(wx.HORIZONTAL)
        user_sizer.Add(wx.StaticText(proxy_panel, label=self._("Username:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.proxy_user = wx.TextCtrl(
            proxy_panel, value=self.prefs.get("proxy_user", ""))
        self.proxy_user.SetName(self._("Username:"))
        user_sizer.Add(self.proxy_user, 1, wx.EXPAND)
        proxy_sizer.Add(user_sizer, 0, wx.EXPAND | wx.ALL, 5)

        pass_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pass_sizer.Add(wx.StaticText(proxy_panel, label=self._("Password:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.proxy_pass = wx.TextCtrl(
            proxy_panel,
            value=self.prefs.get("proxy_password", ""),
            style=wx.TE_PASSWORD,
        )
        self.proxy_pass.SetName(self._("Password:"))
        pass_sizer.Add(self.proxy_pass, 1, wx.EXPAND)
        proxy_sizer.Add(pass_sizer, 0, wx.EXPAND | wx.ALL, 5)
        proxy_panel.SetSizer(proxy_sizer)
        notebook.AddPage(proxy_panel, self._("Proxy"))

        sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.StdDialogButtonSizer()
        btns.AddButton(wx.Button(self, wx.ID_OK))
        btns.AddButton(wx.Button(self, wx.ID_CANCEL))
        btns.Realize()
        sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        self.SetSizer(sizer)
        self.Center()

    def on_browse(self, event):
        dlg = wx.DirDialog(
            self, self._("Choose Download Directory"), self.path_input.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.path_input.SetValue(dlg.GetPath())
        dlg.Destroy()

    def on_browse_watch(self, event):
        dlg = wx.DirDialog(
            self, self._("Choose Watch Folder"), self.watch_input.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.watch_input.SetValue(dlg.GetPath())
        dlg.Destroy()

    def on_reset_rss(self, event):
        if wx.MessageBox(
            self._("Are you sure you want to clear ALL RSS feeds and rules?"),
            self._("Confirm Reset"),
            wx.YES_NO | wx.ICON_WARNING,
        ) != wx.YES:
            return
        try:
            RSSManager().reset_all()
            wx.MessageBox(self._("RSS data reset successfully."), self._("Success"))
        except Exception as exc:  # noqa: BLE001 - UI boundary
            wx.LogError(self._("Reset failed: {error}").format(error=exc))

    def get_preferences(self):
        return {
            "language": _language_value(
                self._language_options, self.language_choice.GetSelection()),
            "download_path": self.path_input.GetValue(),
            "watch_folder": self.watch_input.GetValue().strip(),
            "auto_start": self.auto_start_chk.GetValue(),
            "clipboard_auto_add": self.clipboard_chk.GetValue(),
            "clipboard_prefill": self.clipboard_prefill_chk.GetValue(),
            "min_to_tray": self.min_tray_chk.GetValue(),
            "close_to_tray": self.close_tray_chk.GetValue(),
            "auto_check_updates": self.auto_update_chk.GetValue(),
            "dl_limit": self.dl_limit.GetValue(),
            "ul_limit": self.ul_limit.GetValue(),
            "max_connections": self.max_conn.GetValue(),
            "max_uploads": self.max_slots.GetValue(),
            "listen_port": self.port_input.GetValue(),
            "announce_ip": self.announce_ip_input.GetValue().strip(),
            "listen_interface": self.listen_interface_input.GetValue().strip(),
            "enable_upnp": self.upnp_chk.GetValue(),
            "enable_natpmp": self.natpmp_chk.GetValue(),
            "enable_dht": self.dht_chk.GetValue(),
            "enable_lsd": self.lsd_chk.GetValue(),
            "enable_trackers": self.track_chk.GetValue(),
            "tracker_url": self.track_url_input.GetValue(),
            "rss_update_interval": self.rss_interval.GetValue(),
            "web_ui_enabled": self.web_enabled_chk.GetValue(),
            "web_ui_host": self.web_host.GetValue(),
            "web_ui_port": self.web_port.GetValue(),
            "web_ui_user": self.web_user.GetValue(),
            "web_ui_pass": self.web_pass.GetValue(),
            "proxy_type": self.proxy_type.GetSelection(),
            "proxy_host": self.proxy_host.GetValue(),
            "proxy_port": self.proxy_port.GetValue(),
            "proxy_user": self.proxy_user.GetValue(),
            "proxy_password": self.proxy_pass.GetValue(),
        }
