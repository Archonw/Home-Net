"""GTK3 interface of Pi-Wartung."""
import copy
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import core  # noqa: E402
from .core import Inventory, Pi, Settings, Unraid, UnraidError, human_size  # noqa: E402

FORMATS = [("xz", "Am kleinsten (.img.xz)"), ("gz", "Schneller (.img.gz)"), ("img", "Unkomprimiert (.img)")]
SCHEDULES = [("off", "Aus"), ("daily", "Täglich"), ("weekly", "Wöchentlich"), ("monthly", "Monatlich")]
NOTIFY_OPTIONS = [("errors", "Nur bei Fehlern"), ("always", "Auch bei Erfolg"), ("off", "Aus")]
ROW_CATEGORY, ROW_PI = 0, 1
GREEN, RED, AMBER, BLUE, GRAY = "#2e9e5b", "#d64545", "#d98a1f", "#2f6fed", "#8a8f98"
PASSWORD_HINT = ("Das Passwort des Benutzers auf dem Pi. Es wird nur einmal für die Übertragung genutzt "
                 "und nirgends gespeichert.")


def load_css():
    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css"))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def styled(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


def progress_detail(parts):
    """Turns the detail of a progress line, like '1,234,567 2.93MB/s', into readable text."""
    if len(parts) < 3:
        return ""
    words = parts[2].split()
    if words and words[0].replace(",", "").isdigit():
        words[0] = human_size(int(words[0].replace(",", "")))
    return "  ·  " + "  ·  ".join(words)


def background(func, done=None, error=None):
    """Runs func in a thread and hands the result back to the GTK main loop."""
    def worker():
        try:
            result = func()
        except Exception as exc:  # shown to the user, nothing to recover here
            if error:
                GLib.idle_add(error, str(exc))
            return
        if done:
            GLib.idle_add(done, result)
    threading.Thread(target=worker, daemon=True).start()


def combo(options, active):
    box = Gtk.ComboBoxText()
    for value, label in options:
        box.append(value, label)
    box.set_active_id(active)
    return box


def password_entry():
    entry = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD)
    entry.set_placeholder_text("Leer lassen, um deine eigene SSH-Anmeldung zu nutzen")
    return entry


def form_grid():
    return Gtk.Grid(column_spacing=14, row_spacing=10)


def add_row(grid, row, label, widget, hint=None):
    lab = styled(Gtk.Label(label=label, xalign=1), "row-label")
    lab.set_size_request(150, -1)
    grid.attach(lab, 0, row, 1, 1)
    widget.set_hexpand(True)
    grid.attach(widget, 1, row, 1, 1)
    if hint:
        h = Gtk.Label(label=hint, xalign=0)
        h.set_line_wrap(True)
        h.get_style_context().add_class("dim-label")
        h.set_max_width_chars(60)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(0.85))
        h.set_attributes(attrs)
        grid.attach(h, 1, row + 1, 1, 1)
        return row + 2
    return row + 1


def text_view(text=""):
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text(text)
    view.set_left_margin(6)
    view.set_top_margin(4)
    scroll = Gtk.ScrolledWindow()
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    scroll.set_min_content_height(70)
    scroll.add(view)
    return view, scroll


def view_text(view):
    b = view.get_buffer()
    return b.get_text(b.get_start_iter(), b.get_end_iter(), False)


class PiForm(Gtk.ScrolledWindow):
    """All settings of one Pi; used in the tab and in the 'new Pi' dialog."""

    def __init__(self, inventory, keys, offer_new_key=False):
        super().__init__()
        self.pi = Pi()
        self.new_key = None
        self.password = None
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_border_width(22)
        self.add(outer)
        grid = None
        r = 0

        def section(title):
            nonlocal grid, r
            card = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "card")
            card.pack_start(styled(Gtk.Label(label=title, xalign=0), "card-title"), False, False, 0)
            grid = form_grid()
            card.pack_start(grid, False, False, 0)
            outer.pack_start(card, False, False, 0)
            r = 0

        section("Allgemein")
        self.name = Gtk.Entry()
        r = add_row(grid, r, "Name", self.name)
        self.category = Gtk.ComboBoxText.new_with_entry()
        for c in inventory.all_categories():
            self.category.append_text(c)
        r = add_row(grid, r, "Kategorie", self.category, "Unterkategorien mit /, zum Beispiel Firma Müller/Standort Nord")
        self.notes_view, notes = text_view()
        r = add_row(grid, r, "Notizen", notes)

        section("Verbindung")
        self.host = Gtk.Entry()
        r = add_row(grid, r, "Adresse", self.host, "IP oder Hostname, auch DynDNS oder Tailscale. Das Unraid baut die Verbindung auf.")
        self.port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        r = add_row(grid, r, "Port", self.port)
        self.user = Gtk.Entry()
        r = add_row(grid, r, "Benutzer", self.user, "Braucht sudo ohne Passwort, bei Raspberry Pi OS ist das Standard.")
        self.key = Gtk.ComboBoxText.new_with_entry()
        for k in keys:
            self.key.append_text(k)
        if offer_new_key:
            self.new_key = Gtk.CheckButton(label="Neuen Schlüssel für diesen Pi erzeugen und übertragen")
            self.new_key.set_active(True)
            r = add_row(grid, r, "Zugang", self.new_key)
            self.password = password_entry()
            r = add_row(grid, r, "Passwort", self.password, PASSWORD_HINT)
            self.new_key.connect("toggled", self.on_new_key_toggled)
            self.key.set_sensitive(False)
        r = add_row(grid, r, "SSH-Schlüssel", self.key, "Pfad auf dem Unraid.")
        self.jump = Gtk.Entry()
        self.jump.set_placeholder_text("benutzer@sprunghost, leer für direkte Verbindung")
        r = add_row(grid, r, "Sprunghost", self.jump)
        self.jump_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        r = add_row(grid, r, "Port Sprunghost", self.jump_port)
        self.compress = Gtk.CheckButton(label="Übertragung komprimieren (für Pis im Internet)")
        r = add_row(grid, r, "", self.compress)
        self.bwlimit = Gtk.SpinButton.new_with_range(0, 1000000, 100)
        r = add_row(grid, r, "Bandbreite KiB/s", self.bwlimit, "0 bedeutet unbegrenzt.")

        section("Sicherung")
        self.format = combo(FORMATS, "xz")
        r = add_row(grid, r, "Format", self.format, "Alle drei lassen sich mit Pi Imager und Balena Etcher schreiben.")
        self.keep = Gtk.SpinButton.new_with_range(0, 100, 1)
        r = add_row(grid, r, "Behalten", self.keep, "Anzahl Images, 0 behält alle.")
        self.expand = Gtk.CheckButton(label="Beim ersten Start auf die ganze SD-Karte vergrößern")
        r = add_row(grid, r, "", self.expand)
        self.schedule = combo(SCHEDULES, "off")
        r = add_row(grid, r, "Zeitplan", self.schedule, "Läuft auf dem Unraid über User Scripts, auch wenn dieser Rechner aus ist.")
        self.hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        r = add_row(grid, r, "Frühestens ab Stunde", self.hour)
        self.notify = combo(NOTIFY_OPTIONS, "errors")
        r = add_row(grid, r, "Benachrichtigung", self.notify,
                    "Über das Unraid, also per Mail oder Push, je nachdem wie es dort eingerichtet ist.")
        self.excludes_view, excludes = text_view()
        r = add_row(grid, r, "Ausschlüsse", excludes, "Ein rsync-Muster je Zeile, zusätzlich zu Swap, Caches und /tmp.")
        self.pre = Gtk.Entry()
        self.pre.set_placeholder_text("zum Beispiel sudo systemctl stop datenbank")
        r = add_row(grid, r, "Befehl vorher", self.pre)
        self.post = Gtk.Entry()
        self.post.set_placeholder_text("zum Beispiel sudo systemctl start datenbank")
        r = add_row(grid, r, "Befehl nachher", self.post)
        self.set_size_request(-1, 420)

    def on_new_key_toggled(self, button):
        self.password.set_sensitive(button.get_active())
        self.key.set_sensitive(not button.get_active())

    def load(self, pi):
        self.pi = pi
        self.name.set_text(pi.name)
        self.category.get_child().set_text(pi.category)
        self.notes_view.get_buffer().set_text(pi.notes)
        self.host.set_text(pi.host)
        self.port.set_value(pi.port)
        self.user.set_text(pi.user)
        self.key.get_child().set_text(pi.key)
        self.jump.set_text(pi.jump)
        self.jump_port.set_value(pi.jump_port)
        self.compress.set_active(pi.compress)
        self.bwlimit.set_value(pi.bwlimit)
        self.format.set_active_id(pi.format)
        self.keep.set_value(pi.keep)
        self.expand.set_active(pi.expand)
        self.schedule.set_active_id(pi.schedule)
        self.hour.set_value(pi.hour)
        self.notify.set_active_id(pi.notify)
        self.excludes_view.get_buffer().set_text(pi.excludes)
        self.pre.set_text(pi.pre_cmd)
        self.post.set_text(pi.post_cmd)

    def result(self):
        p = copy.copy(self.pi)
        p.name = self.name.get_text().strip()
        p.category = "/".join(s.strip() for s in self.category.get_active_text().split("/") if s.strip())
        p.notes = view_text(self.notes_view)
        p.host = self.host.get_text().strip()
        p.port = self.port.get_value_as_int()
        p.user = self.user.get_text().strip()
        p.key = self.key.get_active_text().strip()
        if self.new_key and self.new_key.get_active():
            p.key = p.own_key_path()
        p.jump = self.jump.get_text().strip()
        p.jump_port = self.jump_port.get_value_as_int()
        p.compress = self.compress.get_active()
        p.bwlimit = self.bwlimit.get_value_as_int()
        p.format = self.format.get_active_id()
        p.keep = self.keep.get_value_as_int()
        p.expand = self.expand.get_active()
        p.schedule = self.schedule.get_active_id()
        p.hour = self.hour.get_value_as_int()
        p.notify = self.notify.get_active_id()
        p.excludes = view_text(self.excludes_view).strip()
        p.pre_cmd = self.pre.get_text().strip()
        p.post_cmd = self.post.get_text().strip()
        return p


def validate(pi, inventory):
    if not core.valid_segment(pi.name):
        return "Der Name darf nicht leer sein und keine Sonderzeichen wie / oder : enthalten."
    if not core.valid_category(pi.category):
        return "Die Kategorie enthält ungültige Zeichen."
    if not pi.host or re.search(r"\s", pi.host):
        return "Bitte eine Adresse angeben."
    if not pi.user or not pi.key:
        return "Bitte Benutzer und SSH-Schlüssel angeben."
    if inventory.name_taken(pi.name, pi.category, pi.id):
        return "In dieser Kategorie gibt es schon einen Pi mit diesem Namen."
    return None


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Pi-Wartung")
        self.set_default_size(1180, 760)
        self.set_icon_name("drive-harddisk")
        load_css()
        self.settings = Settings.load()
        self.unraid = Unraid(self.settings)
        self.inventory = Inventory()
        self.keys = []
        self.current_id = None
        self.tail = None
        self.tail_for = None
        self.running = set()
        self.states = {}

        header = Gtk.HeaderBar(show_close_button=True, title="Pi-Wartung")
        self.set_titlebar(header)
        add_pi = styled(Gtk.Button(label="Pi hinzufügen"), "suggested-action")
        add_pi.connect("clicked", self.on_add_pi)
        header.pack_start(add_pi)
        add_cat = Gtk.Button(label="Kategorie")
        add_cat.set_tooltip_text("Kategorie hinzufügen")
        add_cat.connect("clicked", self.on_add_category)
        header.pack_start(add_cat)
        settings_btn = Gtk.Button.new_from_icon_name("emblem-system-symbolic", Gtk.IconSize.BUTTON)
        settings_btn.set_tooltip_text("Einstellungen")
        settings_btn.connect("clicked", self.on_settings)
        header.pack_end(settings_btn)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Neu laden")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(paned)

        # columns: display text, kind, pi id, category path, status text
        self.store = Gtk.TreeStore(str, int, str, str, str)
        self.tree = Gtk.TreeView(model=self.store, headers_visible=False)
        col = Gtk.TreeViewColumn()
        name_cell = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END, ypad=7)
        col.pack_start(name_cell, True)
        col.add_attribute(name_cell, "markup", 0)
        col.set_expand(True)
        self.tree.append_column(col)
        status_cell = Gtk.CellRendererText(xalign=1, xpad=10)
        status_col = Gtk.TreeViewColumn("", status_cell, markup=4)
        self.tree.append_column(status_col)
        self.tree.get_selection().connect("changed", self.on_select)
        self.tree.connect("button-press-event", self.on_tree_button)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.tree)
        side = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "sidebar-pane")
        side.set_size_request(300, -1)
        side.pack_start(styled(Gtk.Label(label="RASPBERRY PIS", xalign=0), "sidebar-heading"), False, False, 0)
        side.pack_start(scroll, True, True, 0)
        paned.pack1(side, False, False)

        self.stack = styled(Gtk.Stack(), "content-pane")
        paned.pack2(self.stack, True, False)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        empty.pack_start(styled(Gtk.Label(label="Kein Pi ausgewählt"), "empty-title"), False, False, 0)
        empty.pack_start(styled(Gtk.Label(label="Wähle links einen Pi aus oder füge einen neuen hinzu."), "empty-sub"), False, False, 0)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.build_detail(), "detail")
        self.show_all()
        self.stack.set_visible_child_name("empty")
        self.reload()
        GLib.timeout_add_seconds(20, self.poll_states)

    # ------------------------------------------------------------ layout
    def build_detail(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        hero = styled(Gtk.Box(spacing=10), "hero")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title_row = Gtk.Box(spacing=12)
        self.title_label = styled(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END), "hero-title")
        title_row.pack_start(self.title_label, False, False, 0)
        self.pill = styled(Gtk.Label(valign=Gtk.Align.CENTER), "pill", "pill-muted")
        title_row.pack_start(self.pill, False, False, 0)
        titles.pack_start(title_row, False, False, 0)
        self.subtitle_label = styled(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END), "hero-sub")
        titles.pack_start(self.subtitle_label, False, False, 0)
        hero.pack_start(titles, True, True, 0)
        self.backup_btn = styled(Gtk.Button(label="Jetzt sichern", valign=Gtk.Align.CENTER), "suggested-action")
        self.backup_btn.connect("clicked", self.on_backup)
        self.cancel_btn = Gtk.Button(label="Abbrechen", valign=Gtk.Align.CENTER)
        self.cancel_btn.connect("clicked", self.on_cancel)
        terminal_btn = Gtk.Button(label="Terminal", valign=Gtk.Align.CENTER)
        terminal_btn.connect("clicked", self.on_terminal)
        for w in (self.backup_btn, self.cancel_btn, terminal_btn):
            hero.pack_end(w, False, False, 0)
        box.pack_start(hero, False, False, 0)

        self.run_box = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "card", "run-box")
        self.run_label = styled(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END), "card-title")
        self.progress = Gtk.ProgressBar()
        self.run_box.pack_start(self.run_label, False, False, 0)
        self.run_box.pack_start(self.progress, False, False, 0)
        self.run_box.set_no_show_all(True)
        self.run_label.show()
        self.progress.show()
        box.pack_start(self.run_box, False, False, 0)

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        switcher = styled(Gtk.StackSwitcher(stack=self.pages, halign=Gtk.Align.START), "page-switcher")
        box.pack_start(switcher, False, False, 0)
        box.pack_start(self.pages, True, True, 0)
        self.pages.add_titled(self.build_overview(), "overview", "Übersicht")
        self.pages.add_titled(self.build_images(), "images", "Sicherungen")
        self.form_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pages.add_titled(self.form_holder, "config", "Konfiguration")
        self.pages.add_titled(self.build_log(), "log", "Protokoll")
        self.pages.connect("notify::visible-child-name", self.on_page)
        return box

    def build_overview(self):
        scroll = Gtk.ScrolledWindow()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_border_width(26)
        outer.set_margin_top(6)
        scroll.add(outer)
        self.tiles = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12,
                                 homogeneous=True, min_children_per_line=2, max_children_per_line=4)
        outer.pack_start(self.tiles, False, False, 0)
        self.details_card = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8), "card")
        outer.pack_start(self.details_card, False, False, 0)
        btns = Gtk.Box(spacing=8)
        b = Gtk.Button(label="Zustand abfragen")
        b.connect("clicked", self.on_check)
        btns.pack_start(b, False, False, 0)
        b = Gtk.Button(label="Neuen Schlüssel einrichten")
        b.set_tooltip_text("Erzeugt einen neuen Schlüssel für diesen Pi und überträgt ihn, auf Wunsch mit Passwort")
        b.connect("clicked", self.on_setup_access)
        btns.pack_start(b, False, False, 0)
        outer.pack_start(btns, False, False, 0)
        return scroll

    def build_images(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(26)
        box.set_margin_top(6)
        # columns: file, date, size, full size, info
        self.image_store = Gtk.ListStore(str, str, str, str, str)
        self.image_view = Gtk.TreeView(model=self.image_store)
        for i, title in enumerate(("Datei", "Datum", "Größe", "Auf der Karte", "Quelle")):
            c = Gtk.TreeViewColumn(title, Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END), text=i)
            c.set_resizable(True)
            c.set_expand(i in (0, 4))
            self.image_view.append_column(c)
        scroll = Gtk.ScrolledWindow()
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.image_view)
        box.pack_start(scroll, True, True, 0)
        row = Gtk.Box(spacing=8)
        for label, handler in (("In Pi Imager öffnen", self.on_imager), ("Ordner öffnen", self.on_open_folder),
                               ("Pfad kopieren", self.on_copy_path), ("Löschen", self.on_delete_image)):
            b = Gtk.Button(label=label)
            b.connect("clicked", handler)
            row.pack_start(b, False, False, 0)
        box.pack_start(row, False, False, 0)
        return box

    def build_log(self):
        self.log_view = styled(Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR), "log-view")
        scroll = Gtk.ScrolledWindow()
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.log_view)
        frame = Gtk.Box()
        frame.set_border_width(26)
        frame.set_margin_top(6)
        frame.pack_start(scroll, True, True, 0)
        return frame

    # ------------------------------------------------------------ data
    def error(self, message):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.CLOSE, text=message)
        dlg.run()
        dlg.destroy()

    def confirm(self, text, detail=""):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text=text, secondary_text=detail)
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return ok

    def reload(self):
        def work():
            self.unraid.deploy_engine()
            inv = self.unraid.load_inventory()
            keys = self.unraid.list_keys()
            return inv, keys

        def done(result):
            self.inventory, self.keys = result
            self.refresh_tree()
            self.poll_states()

        background(work, done, lambda e: self.error(f"Unraid nicht erreichbar:\n{e}"))

    def refresh_tree(self):
        selected = self.current_id
        self.store.clear()
        nodes = {"": None}
        for cat in self.inventory.all_categories():
            parent = cat.rsplit("/", 1)[0] if "/" in cat else ""
            title = GLib.markup_escape_text(cat.rsplit("/", 1)[-1])
            nodes[cat] = self.store.append(nodes.get(parent), [f'<span weight="600">{title}</span>', ROW_CATEGORY, "", cat, ""])
        select_iter = None
        for p in sorted(self.inventory.pis, key=lambda x: x.name.casefold()):
            it = self.store.append(nodes.get(p.category), [GLib.markup_escape_text(p.name), ROW_PI, p.id,
                                                           p.category, self.status_text(p.id)])
            if p.id == selected:
                select_iter = it
        self.tree.expand_all()
        if select_iter:
            self.tree.get_selection().select_iter(select_iter)

    def status_text(self, pi_id):
        st = self.states.get(pi_id)
        if not st:
            return ""
        if st.get("RUNNING") == "1":
            return f'<span foreground="{BLUE}">● läuft</span>'
        last = st.get("LAST_STATUS")
        if last == "ok":
            days = (time.time() - int(st.get("LAST_TIME", "0"))) / 86400
            age = "heute" if days < 1 else f"vor {int(days)} T"
            return f'<span foreground="{GREEN}">● {age}</span>'
        if last == "failed":
            return f'<span foreground="{RED}">● Fehler</span>'
        if last == "cancelled":
            return f'<span foreground="{AMBER}">● Abbruch</span>'
        return f'<span foreground="{GRAY}">○ nie</span>'

    def poll_states(self):
        pis = list(self.inventory.pis)

        def work():
            out = {}
            for p in pis:
                try:
                    out[p.id] = self.unraid.state(p)
                except UnraidError:
                    pass  # no folder yet, or a server hiccup; next poll will tell
            return out

        def done(states):
            self.states = states
            self.update_status_cells()
            self.update_buttons()
            self.attach_if_running()

        background(work, done)
        return True

    def update_status_cells(self):
        def visit(model, path, it):
            if model[it][1] == ROW_PI:
                model[it][4] = self.status_text(model[it][2])
            return False
        self.store.foreach(visit)

    @property
    def current(self):
        return self.inventory.find(self.current_id) if self.current_id else None

    # ------------------------------------------------------------ selection
    def on_select(self, selection):
        model, it = selection.get_selected()
        if not it or model[it][1] != ROW_PI:
            return
        if model[it][2] == self.current_id:
            return
        self.current_id = model[it][2]
        self.show_pi()

    def show_pi(self):
        pi = self.current
        if not pi:
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("detail")
        self.title_label.set_text(pi.name)
        self.subtitle_label.set_text(f"{pi.category or 'Ohne Kategorie'}  ·  {pi.user}@{pi.host}:{pi.port}")
        self.render_status({})
        self.stop_tail()
        self.log_view.get_buffer().set_text("")
        self.run_box.hide()
        self.build_form(pi)
        self.update_buttons()
        self.load_images()
        self.load_log()
        self.attach_if_running()
        self.on_check()

    def build_form(self, pi):
        for c in self.form_holder.get_children():
            self.form_holder.remove(c)
        self.form = PiForm(self.inventory, self.keys)
        self.form.load(pi)
        self.form_holder.pack_start(self.form, True, True, 0)
        row = styled(Gtk.Box(spacing=8), "action-bar")
        save = Gtk.Button(label="Speichern")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self.on_save_form)
        delete = Gtk.Button(label="Pi entfernen")
        delete.get_style_context().add_class("destructive-action")
        delete.connect("clicked", self.on_delete_pi)
        row.pack_end(save, False, False, 0)
        row.pack_start(delete, False, False, 0)
        self.form_holder.pack_start(row, False, False, 0)
        self.form_holder.show_all()

    def on_page(self, stack, _param):
        if stack.get_visible_child_name() == "images" and self.current:
            self.load_images()

    def update_buttons(self):
        st = self.states.get(self.current_id, {})
        running = st.get("RUNNING") == "1"
        self.backup_btn.set_sensitive(not running)
        self.cancel_btn.set_visible(running)
        self.update_pill(st)

    # ------------------------------------------------------------ overview
    def update_pill(self, st):
        ctx = self.pill.get_style_context()
        for c in ("pill-ok", "pill-bad", "pill-warn", "pill-info", "pill-muted"):
            ctx.remove_class(c)
        last = st.get("LAST_STATUS")
        if st.get("RUNNING") == "1":
            text, cls = "Sicherung läuft", "pill-info"
        elif last == "ok":
            text, cls = "Gesichert", "pill-ok"
        elif last == "failed":
            text, cls = "Letzte Sicherung fehlgeschlagen", "pill-bad"
        elif last == "cancelled":
            text, cls = "Abgebrochen", "pill-warn"
        else:
            text, cls = "Noch nicht gesichert", "pill-muted"
        self.pill.set_text(text)
        ctx.add_class(cls)

    def add_tile(self, label, value, detail="", tone=None, level=None):
        card = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4), "card")
        card.pack_start(styled(Gtk.Label(label=label, xalign=0), "card-label"), False, False, 0)
        val = styled(Gtk.Label(label=value, xalign=0, ellipsize=Pango.EllipsizeMode.END), "card-value")
        if tone:
            styled(val, f"value-{tone}")
        card.pack_start(val, False, False, 0)
        if level is not None:
            bar = Gtk.LevelBar(min_value=0, max_value=100, value=level)
            bar.set_margin_top(4)
            card.pack_start(bar, False, False, 0)
        if detail:
            card.pack_start(styled(Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                             tooltip_text=detail), "card-detail"), False, False, 0)
        child = Gtk.FlowBoxChild(can_focus=False)
        child.add(card)
        self.tiles.add(child)

    def render_status(self, info, error=None):
        for c in self.tiles.get_children():
            self.tiles.remove(c)
        for c in self.details_card.get_children():
            self.details_card.remove(c)
        st = self.states.get(self.current_id, {})

        if error:
            self.add_tile("Erreichbarkeit", "Offline", error, tone="bad")
        elif info:
            self.add_tile("Erreichbarkeit", "Online", info.get("ip", ""), tone="ok")
        else:
            self.add_tile("Erreichbarkeit", "Wird abgefragt …")

        if st.get("LAST_TIME"):
            when = time.strftime("%d.%m.%Y, %H:%M", time.localtime(int(st["LAST_TIME"])))
            label, tone = {"ok": ("Erfolgreich", "ok"), "failed": ("Fehlgeschlagen", "bad"),
                           "cancelled": ("Abgebrochen", "warn")}.get(st.get("LAST_STATUS"), ("Unbekannt", None))
            detail = when
            if st.get("LAST_STATUS") == "failed" and st.get("LAST_ERROR"):
                detail = st["LAST_ERROR"]
            elif st.get("LAST_WARNINGS"):
                detail = st["LAST_WARNINGS"]
            self.add_tile("Letzte Sicherung", label, detail, tone=tone)
        else:
            self.add_tile("Letzte Sicherung", "Noch keine")

        if info:
            parts = info.get("disk", "").split()
            pct = int(parts[2].rstrip("%")) if len(parts) == 3 and parts[2].rstrip("%").isdigit() else None
            detail = f"{parts[0]} von {parts[1]} belegt" if pct is not None else ""
            self.add_tile("SD-Karte", f"{pct} %" if pct is not None else "?", detail, level=pct)
            temp = info.get("temp", "")
            digits = temp.split(" ")[0]
            tone = None
            if digits.isdigit():
                tone = "bad" if int(digits) >= 80 else "warn" if int(digits) >= 70 else None
            self.add_tile("Temperatur", temp or "?", f"Last {info.get('load', '')}", tone=tone)

        rows = []
        if info:
            rows += [("Modell", info.get("model", "")), ("System", info.get("os", "")), ("Kernel", info.get("kernel", "")),
                     ("Hostname", info.get("hostname", "")), ("Laufzeit", info.get("uptime", "")),
                     ("Arbeitsspeicher", info.get("mem", "")),
                     ("sudo / rsync", f"{info.get('sudo', '?')} / {info.get('rsync', '?')}"),
                     ("Neustart nötig", info.get("reboot", ""))]
        if st.get("MIRROR_BYTES"):
            rows.append(("Spiegel auf Unraid", human_size(int(st["MIRROR_BYTES"]))))
        pi = self.current
        if pi and pi.notes:
            rows.append(("Notizen", pi.notes))
        if rows:
            self.details_card.pack_start(styled(Gtk.Label(label="Details", xalign=0), "card-title"), False, False, 0)
            grid = Gtk.Grid(column_spacing=18, row_spacing=7)
            for i, (k, v) in enumerate(rows):
                lab = styled(Gtk.Label(label=k, xalign=0), "row-label")
                lab.set_valign(Gtk.Align.START)
                val = Gtk.Label(label=v, xalign=0, selectable=True, wrap=True)
                val.set_hexpand(True)
                grid.attach(lab, 0, i, 1, 1)
                grid.attach(val, 1, i, 1, 1)
            self.details_card.pack_start(grid, False, False, 0)
            self.details_card.show_all()
            self.details_card.show()
        else:
            self.details_card.hide()
        self.tiles.show_all()

    def on_check(self, *_):
        pi = self.current
        if not pi:
            return
        pid = pi.id
        self.render_status({})

        def done(info):
            if self.current_id == pid:
                self.render_status(info)

        def fail(msg):
            if self.current_id == pid:
                self.render_status({}, error=msg)

        background(lambda: self.unraid.status(pi), done, fail)

    def on_setup_access(self, *_):
        pi = self.current
        if not pi:
            return
        dlg = Gtk.Dialog(title="Neuen SSH-Schlüssel einrichten", transient_for=self, modal=True)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Einrichten", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        grid = form_grid()
        info = Gtk.Label(xalign=0, label=f"Das Unraid bekommt einen eigenen Schlüssel für {pi.name}. Sein öffentlicher Teil "
                                         f"wird bei {pi.user}@{pi.host} eingetragen. Sonst ändert sich am Pi nichts.")
        info.set_line_wrap(True)
        info.set_max_width_chars(60)
        grid.attach(info, 0, 0, 2, 1)
        user = Gtk.Entry(text=pi.user)
        r = add_row(grid, 1, "Benutzer", user)
        password = password_entry()
        password.set_activates_default(True)
        add_row(grid, r, "Passwort", password, PASSWORD_HINT)
        dlg.get_content_area().pack_start(grid, True, True, 0)
        dlg.show_all()
        answer, username, secret = dlg.run(), user.get_text().strip(), password.get_text()
        dlg.destroy()
        if answer != Gtk.ResponseType.OK or not username:
            return
        fresh = copy.copy(pi)
        fresh.user = username
        fresh.key = fresh.own_key_path()
        self.install_key(pi, fresh, secret)

    def install_key(self, old, fresh, secret):
        """Installs the key of fresh; only then does the Pi use it from now on."""
        def work():
            if secret:
                return self.unraid.install_key_with_password(fresh, secret)
            return self.unraid.install_key_from_desktop(fresh)

        def done(result):
            def after():
                self.on_check()
                if result.get("sudo") != "ok":
                    self.error(f"Der Schlüssel funktioniert, aber {fresh.user} darf sudo nicht ohne Passwort nutzen. "
                               "Für Sicherungen ist das nötig.")
            self.apply_pi(old, fresh, after)

        def fail(message):
            if old:
                self.unraid_restore_conf(old)
            self.error(f"Der Schlüssel konnte nicht eingerichtet werden:\n{message}")

        background(work, done, fail)

    def unraid_restore_conf(self, pi):
        background(lambda: self.unraid.write_conf(pi))

    def on_terminal(self, *_):
        pi = self.current
        if not pi:
            return
        terminal = next((t for t in ("x-terminal-emulator", "gnome-terminal", "xterm") if shutil.which(t)), None)
        if not terminal:
            self.error("Kein Terminalprogramm gefunden.")
            return
        cmd = pi.ssh_command()
        args = [terminal, "--", *cmd] if terminal == "gnome-terminal" else [terminal, "-e", " ".join(map(shlex.quote, cmd))]
        subprocess.Popen(args, start_new_session=True)

    # ------------------------------------------------------------ backup
    def on_backup(self, *_):
        pi = self.current
        if not pi:
            return
        self.backup_btn.set_sensitive(False)
        self.states.setdefault(pi.id, {})["RUNNING"] = "1"

        def work():
            self.unraid.deploy_engine()
            self.unraid.write_conf(pi)
            return self.unraid.start_backup(pi)

        def done(logfile):
            self.log_view.get_buffer().set_text("")
            self.pages.set_visible_child_name("log")
            self.start_tail(pi.id, logfile)
            self.update_buttons()
            self.update_status_cells()

        def fail(msg):
            self.states[pi.id]["RUNNING"] = "0"
            self.update_buttons()
            self.error(f"Sicherung konnte nicht gestartet werden:\n{msg}")

        background(work, done, fail)

    def on_cancel(self, *_):
        pi = self.current
        if pi and self.confirm("Sicherung abbrechen?", "Vorhandene Images bleiben unverändert. Der Abgleich macht beim nächsten Mal dort weiter."):
            background(lambda: self.unraid.cancel(pi), None, self.error)

    def attach_if_running(self):
        st = self.states.get(self.current_id, {})
        if st.get("RUNNING") == "1" and self.tail_for != self.current_id and st.get("LOGFILE"):
            self.log_view.get_buffer().set_text("")
            self.start_tail(self.current_id, st["LOGFILE"])

    def load_log(self):
        pi = self.current
        st = self.states.get(pi.id, {}) if pi else {}
        path = st.get("LOGFILE")
        if not path or st.get("RUNNING") == "1":
            return
        pid = pi.id

        def done(text):
            if self.current_id != pid or self.tail_for:
                return
            self.log_view.get_buffer().set_text("")
            for line in text.splitlines():
                self.handle_line(pid, line, replay=True)

        background(lambda: self.unraid.run(f"tail -n 400 {shlex.quote(path)}"), done)

    def start_tail(self, pi_id, logfile):
        self.stop_tail()
        self.tail_for = pi_id
        self.run_box.show()
        self.progress.set_fraction(0)
        self.run_label.set_text("Startet …")
        proc = self.unraid.tail_process(logfile)
        self.tail = proc

        def reader():
            for line in proc.stdout:
                GLib.idle_add(self.handle_line, pi_id, line.rstrip("\n"))
        threading.Thread(target=reader, daemon=True).start()

    def stop_tail(self):
        if self.tail:
            self.tail.terminate()
        self.tail = None
        self.tail_for = None

    def handle_line(self, pi_id, line, replay=False):
        if pi_id != self.current_id:
            return False
        if line.startswith("@@STEP"):
            parts = line.split(" ", 3)
            self.step_label = f"Schritt {parts[1]} von {parts[2]}: {parts[3]}"
            self.progress.set_fraction(0)
            self.run_label.set_text(self.step_label)
        elif line.startswith("@@PROGRESS"):
            parts = line.split(" ", 2)
            pct = min(int(parts[1]), 100) if parts[1].isdigit() else 0
            self.progress.set_fraction(pct / 100)
            self.run_label.set_text(f"{getattr(self, 'step_label', '')}  ·  {pct} %{progress_detail(parts)}")
        elif line.startswith("@@DONE"):
            if not replay:
                self.finish_tail(pi_id, line.split()[-1])
        else:
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), line + "\n")
            self.log_view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
        return False

    def finish_tail(self, pi_id, status):
        self.stop_tail()
        self.run_box.hide()
        self.poll_states()
        self.load_images()
        if status == "failed":
            self.error("Die Sicherung ist fehlgeschlagen. Details stehen im Protokoll.")

    # ------------------------------------------------------------ images
    def load_images(self):
        pi = self.current
        if not pi:
            return
        pid = pi.id

        def done(rows):
            if self.current_id != pid:
                return
            self.image_store.clear()
            for r in rows:
                info = r["info"]
                raw = int(info["raw_bytes"]) if info.get("raw_bytes", "").isdigit() else 0
                self.image_store.append([r["file"], time.strftime("%d.%m.%Y %H:%M", time.localtime(r["mtime"])),
                                         human_size(r["size"]), human_size(raw) if raw else "",
                                         info.get("os", "")])

        background(lambda: self.unraid.images(pi), done, lambda e: self.image_store.clear())

    def selected_image(self):
        model, it = self.image_view.get_selection().get_selected()
        return model[it][0] if it else None

    def local_image_path(self, filename):
        return self.settings.to_local(f"{self.unraid.pidir(self.current)}/{filename}")

    def on_imager(self, *_):
        f = self.selected_image()
        if not f:
            self.error("Bitte zuerst eine Sicherung auswählen.")
            return
        path = self.local_image_path(f)
        if not os.path.exists(path):
            self.error(f"Die Datei ist auf diesem Rechner nicht erreichbar:\n{path}\n\nPrüfe unter Einstellungen die Pfadzuordnung.")
            return
        if not shutil.which("rpi-imager"):
            self.error("Raspberry Pi Imager ist nicht installiert.")
            return
        subprocess.Popen(["rpi-imager", path], start_new_session=True)

    def on_open_folder(self, *_):
        if self.current:
            subprocess.Popen(["xdg-open", self.settings.to_local(self.unraid.pidir(self.current))], start_new_session=True)

    def on_copy_path(self, *_):
        f = self.selected_image()
        if f:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.local_image_path(f), -1)

    def on_delete_image(self, *_):
        f = self.selected_image()
        pi = self.current
        if f and pi and self.confirm(f"{f} wirklich löschen?", "Das lässt sich nicht rückgängig machen."):
            background(lambda: self.unraid.delete_image(pi, f), lambda _: self.load_images(), self.error)

    # ------------------------------------------------------------ add, edit, remove
    def apply_pi(self, old, new, then=None):
        """Stores an edited or new Pi on the server and in the list."""
        inv = self.inventory

        def work():
            if old:
                self.unraid.move_pi(old, new)
            if new.schedule != "off":
                self.unraid.install_user_script()
            pis = [new if p.id == new.id else p for p in inv.pis]
            if not old:
                pis.append(new)
            cats = sorted(set(inv.categories) | ({new.category} if new.category else set()))
            fresh = Inventory(categories=cats, pis=pis)
            self.unraid.save_inventory(fresh)
            self.unraid.write_conf(new)
            return fresh

        def done(fresh):
            self.inventory = fresh
            self.current_id = new.id
            self.refresh_tree()
            self.show_pi()
            if then:
                then()

        background(work, done, lambda e: self.error(f"Speichern fehlgeschlagen:\n{e}"))

    def on_save_form(self, *_):
        old = self.current
        new = self.form.result()
        problem = validate(new, self.inventory)
        if problem:
            self.error(problem)
            return
        if old and (self.states.get(old.id, {}).get("RUNNING") == "1") and self.unraid.pidir(old) != self.unraid.pidir(new):
            self.error("Während einer Sicherung lassen sich Name und Kategorie nicht ändern.")
            return
        self.apply_pi(old, new)

    def on_add_pi(self, *_):
        dlg = Gtk.Dialog(title="Pi hinzufügen", transient_for=self, modal=True)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Hinzufügen", Gtk.ResponseType.OK)
        dlg.set_default_size(620, 640)
        form = PiForm(self.inventory, self.keys, offer_new_key=True)
        new = Pi()
        form.load(new)
        area = dlg.get_content_area()
        hosts = core.ssh_config_hosts()
        if hosts:
            row = Gtk.Box(spacing=8)
            row.set_border_width(10)
            row.pack_start(Gtk.Label(label="Aus SSH-Konfiguration übernehmen:"), False, False, 0)
            pick = Gtk.ComboBoxText()
            pick.append("", "–")
            for h in sorted(hosts):
                pick.append(h, h)
            pick.set_active_id("")

            def on_pick(box):
                h = hosts.get(box.get_active_id() or "")
                if not h:
                    return
                form.name.set_text(box.get_active_id().capitalize())
                form.host.set_text(h["host"])
                form.port.set_value(h["port"])
                form.user.set_text(h["user"])
                # A key of the same file name on the server is most likely the matching one.
                base = os.path.basename(h["identity"])
                match = next((k for k in self.keys if os.path.basename(k) == base), None)
                if match:
                    form.key.get_child().set_text(match)
                    form.new_key.set_active(False)

            pick.connect("changed", on_pick)
            row.pack_start(pick, True, True, 0)
            area.pack_start(row, False, False, 0)
        area.pack_start(form, True, True, 0)
        sel_model, sel_it = self.tree.get_selection().get_selected()
        if sel_it:
            form.category.get_child().set_text(sel_model[sel_it][3])
        dlg.show_all()
        while dlg.run() == Gtk.ResponseType.OK:
            pi = form.result()
            problem = validate(pi, self.inventory)
            if problem:
                self.error(problem)
                continue
            wants_key = form.new_key.get_active()
            secret = form.password.get_text()
            dlg.destroy()
            if wants_key:
                # Saved first, so that a failed transfer can simply be repeated from the overview.
                self.apply_pi(None, pi, lambda: self.install_key(pi, pi, secret))
            else:
                self.apply_pi(None, pi)
            return
        dlg.destroy()

    def ask_text(self, title, label, initial=""):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=initial, activates_default=True)
        area = dlg.get_content_area()
        area.set_spacing(8)
        area.set_border_width(12)
        area.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        area.pack_start(entry, False, False, 0)
        dlg.show_all()
        value = entry.get_text().strip() if dlg.run() == Gtk.ResponseType.OK else None
        dlg.destroy()
        return value

    def save_inventory_async(self):
        inv = self.inventory
        background(lambda: self.unraid.save_inventory(inv), lambda _: self.refresh_tree(), self.error)

    def on_add_category(self, *_):
        model, it = self.tree.get_selection().get_selected()
        prefix = model[it][3] + "/" if it and model[it][3] else ""
        value = self.ask_text("Kategorie hinzufügen", "Name, Unterkategorien mit /, zum Beispiel Firma Müller/Standort Nord:", prefix)
        if not value:
            return
        value = "/".join(s.strip() for s in value.split("/") if s.strip())
        if not core.valid_category(value):
            self.error("Der Name enthält ungültige Zeichen.")
            return
        if value not in self.inventory.categories:
            self.inventory.categories.append(value)
        self.save_inventory_async()

    def on_tree_button(self, tree, event):
        if event.button != 3:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if not hit:
            return False
        row = self.store[hit[0]]
        if row[1] != ROW_CATEGORY:
            return False
        cat = row[3]
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="Kategorie umbenennen")
        item.connect("activate", lambda *_: self.rename_category(cat))
        menu.append(item)
        item = Gtk.MenuItem(label="Kategorie entfernen")
        item.connect("activate", lambda *_: self.remove_category(cat))
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def in_category(self, pi, cat):
        return pi.category == cat or pi.category.startswith(cat + "/")

    def rename_category(self, cat):
        value = self.ask_text("Kategorie umbenennen", "Neuer Pfad:", cat)
        if not value or value == cat:
            return
        value = "/".join(s.strip() for s in value.split("/") if s.strip())
        if not core.valid_category(value):
            self.error("Der Name enthält ungültige Zeichen.")
            return
        affected = [p for p in self.inventory.pis if self.in_category(p, cat)]
        if any(self.states.get(p.id, {}).get("RUNNING") == "1" for p in affected):
            self.error("In dieser Kategorie läuft gerade eine Sicherung.")
            return
        inv = self.inventory

        def work():
            for p in affected:
                new = copy.copy(p)
                new.category = value + p.category[len(cat):]
                self.unraid.move_pi(p, new)
                self.unraid.write_conf(new)
                p.category = new.category
            inv.categories = [value + c[len(cat):] if c == cat or c.startswith(cat + "/") else c for c in inv.categories]
            self.unraid.save_inventory(inv)

        background(work, lambda _: (self.refresh_tree(), self.show_pi()), self.error)

    def remove_category(self, cat):
        if any(self.in_category(p, cat) for p in self.inventory.pis):
            self.error("In dieser Kategorie liegen noch Pis. Bitte erst verschieben oder entfernen.")
            return
        self.inventory.categories = [c for c in self.inventory.categories if not (c == cat or c.startswith(cat + "/"))]
        self.save_inventory_async()

    def on_delete_pi(self, *_):
        pi = self.current
        if not pi:
            return
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                text=f"{pi.name} aus der Liste entfernen?",
                                secondary_text="Auf Wunsch werden auch alle Images und der Spiegel auf dem Unraid gelöscht. Das lässt sich nicht rückgängig machen.")
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Nur aus der Liste", 1, "Auch alle Daten löschen", 2)
        answer = dlg.run()
        dlg.destroy()
        if answer not in (1, 2):
            return
        inv = self.inventory

        def work():
            inv.pis = [p for p in inv.pis if p.id != pi.id]
            if answer == 2:
                self.unraid.delete_pi_data(pi)
            self.unraid.save_inventory(inv)

        def done(_):
            self.current_id = None
            self.stop_tail()
            self.refresh_tree()
            self.stack.set_visible_child_name("empty")

        background(work, done, self.error)

    def on_settings(self, *_):
        dlg = Gtk.Dialog(title="Einstellungen", transient_for=self, modal=True)
        dlg.add_buttons("Abbrechen", Gtk.ResponseType.CANCEL, "Speichern", Gtk.ResponseType.OK)
        grid = form_grid()
        s = self.settings
        ssh = Gtk.Entry(text=s.unraid_ssh)
        r = add_row(grid, 0, "Unraid per SSH", ssh, "Eintrag aus ~/.ssh/config oder root@adresse.")
        root = Gtk.Entry(text=s.root)
        r = add_row(grid, r, "Sicherungsordner", root, "Pfad auf dem Unraid.")
        remote = Gtk.Entry(text=s.remote_prefix)
        r = add_row(grid, r, "Pfad auf Unraid", remote)
        local = Gtk.Entry(text=s.local_prefix)
        r = add_row(grid, r, "Hier eingehängt als", local, "Damit die App Images auf diesem Rechner findet.")
        dlg.get_content_area().pack_start(grid, True, True, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            s.unraid_ssh, s.root = ssh.get_text().strip(), root.get_text().strip().rstrip("/")
            s.remote_prefix, s.local_prefix = remote.get_text().strip().rstrip("/"), local.get_text().strip().rstrip("/")
            s.save()
            self.unraid = Unraid(s)
            self.current_id = None
            self.stop_tail()
            self.stack.set_visible_child_name("empty")
            self.reload()
        dlg.destroy()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="de.archonw.PiWartung")

    def do_activate(self):
        win = self.props.active_window or MainWindow(self)
        win.present()


def main():
    return App().run(None)
