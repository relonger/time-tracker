import gi
import logging

gi.require_version('Gtk', '3.0')
try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as AppIndicator
except Exception:
    try:
        gi.require_version('AyatanaAppIndicator3', '0.1')
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except Exception:  # pragma: no cover
        AppIndicator = None

from gi.repository import Gtk, GLib, Gdk
import datetime as dt
from typing import List, Optional, Dict, Any

from .storage import load_all, save_data, save_settings, new_empty_data, make_task_dict
from .model import Task, task_from_dict, task_to_dict, find_task_by_id, stop_all, new_task
from .hotkeys import GlobalHotkeys
from .notify import show as notify_show
from .util import parse_duration_delta, humanize_seconds, now, day_start
from .ui import TTrackerWindow
from .report import open_report_flow

logger = logging.getLogger(__name__)


class App:
    # Distinct color palette (hex) — combined from matplotlib tab10 + selected tab20 entries + Set3-like tones
    _PALETTE = [
        # tab10
        '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
        '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
        # extra saturated selections (tab20 first of pairs)
        '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
        '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
        # Set3-like (avoid very light yellow/white-ish later)
        '#8dd3c7', '#bebada', '#fb8072', '#80b1d3', '#fdb462',
        '#b3de69', '#fccde5', '#bc80bd', '#ccebc5', '#ffed6f',
        '#a6cee3', '#1f78b4', '#b2df8a', '#33a02c', '#fb9a99',
        '#e31a1c', '#fdbf6f', '#ff7f00', '#cab2d6', '#6a3d9a',
    ]

    def __init__(self):
        st = load_all()
        self.data = st.data or new_empty_data()
        self.settings = st.settings
        self.roots: List[Task] = [task_from_dict(d) for d in self.data.get('tasks', [])]
        self.active_task: Optional[Task] = None

        # Ensure every task has a persistent distinct color
        if self._ensure_task_colors():
            # Persist immediately so subsequent runs stay stable
            try:
                self.data['tasks'] = [task_to_dict(t) for t in self.roots]
                save_data(self.data)
            except Exception:
                pass
        self.indicator = None
        self.window: Optional[TTrackerWindow] = None
        self.hotkeys = GlobalHotkeys()
        self._goal_notified_today: Dict[str, dt.date] = {}

        # UI
        self.window = TTrackerWindow(
            self.roots,
            on_toggle_task=self.toggle_task,
            on_adjust_task=self.adjust_task_dialog,
            on_set_goal=self.set_goal_dialog,
            on_set_hotkey=self.assign_hotkey_dialog,
            on_show_report=self.open_report,
            on_save=self.save_all,
            hotkey_lookup=self._get_task_hotkey,
        )
        # Track window geometry/state to preserve position/size across hide/show
        self._last_geometry = {}
        self._window_is_maximized = False
        self.window.connect('configure-event', self._on_window_configure)
        self.window.connect('window-state-event', self._on_window_state)
        self.window.connect('delete-event', self._on_window_delete)

        # Indicator
        self._setup_indicator()

        # Hotkeys
        self._bind_all_hotkeys()
        # Global app toggle
        app_accel = self.settings.get('app_hotkey', '')
        ok = self.hotkeys.bind(app_accel, self.toggle_window)
        if ok:
            logger.debug("Bound app hotkey '%s' for show/hide", app_accel)
        else:
            logger.warning("Failed to bind app hotkey '%s'. If you are on Wayland, global hotkeys may be restricted.", app_accel)

        # Autosave every 30min
        GLib.timeout_add_seconds(1800, self._autosave_tick)

        # Periodic check for goal reached while running (every 30s)
        GLib.timeout_add_seconds(30, self._goal_check_tick)

    def _get_task_hotkey(self, task: Task) -> str:
        try:
            return self.settings.get('task_hotkeys', {}).get(task.id, '')
        except Exception:
            return ''

    def _on_window_delete(self, *args):
        logger.debug("Window close requested -> quitting application")
        self.quit()
        return True

    def _setup_indicator(self):
        if AppIndicator is None:
            logger.warning("AppIndicator not available; tray icon will not be shown. Install 'gir1.2-appindicator3-0.1' or 'gir1.2-ayatanaappindicator3-0.1' and ensure the GNOME AppIndicator extension is enabled.")
            return
        logger.debug("Setting up AppIndicator")
        self.indicator = AppIndicator.Indicator.new(
            "ttracker-indicator",
            "appointment-new",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        # Try to enforce a known-good icon name
        try:
            if hasattr(self.indicator, 'set_icon_full'):
                self.indicator.set_icon_full("appointment-new", "TTracker")
            elif hasattr(self.indicator, 'set_icon'):
                self.indicator.set_icon("appointment-new")
        except Exception as e:
            logger.debug("Failed to set indicator icon explicitly: %s", e)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        menu = Gtk.Menu()
        # Open item to allow opening the window explicitly
        mi_open = Gtk.MenuItem.new_with_label("Open TTracker")
        mi_open.connect('activate', lambda *_: self.show_window())
        menu.append(mi_open)
        # Quit item
        mi_quit = Gtk.MenuItem.new_with_label("Quit")
        mi_quit.connect('activate', lambda *_: self.quit())
        menu.append(mi_quit)
        # Toggle window on left-click by reacting to the menu being mapped (shown)
        try:
            def _on_menu_map(m, *a):
                logger.debug("Tray menu mapped -> toggle window")
                GLib.idle_add(self.toggle_window)
                return False
            menu.connect('map', _on_menu_map)
        except Exception:
            pass
        # If supported, enable middle-click to activate "Open"
        try:
            if hasattr(self.indicator, 'set_secondary_activate_target'):
                self.indicator.set_secondary_activate_target(mi_open)
        except Exception:
            pass
        menu.show_all()
        self.indicator.set_menu(menu)
        logger.debug("AppIndicator set up with icon 'appointment-new' and menu Open/Quit")

    def _apply_saved_geometry(self) -> bool:
        wcfg = self.settings.get('window', {}) or {}
        maximize = bool(wcfg.get('maximized', False))
        try:
            # Only apply size/position if not maximizing
            if not maximize:
                w = wcfg.get('width')
                h = wcfg.get('height')
                if isinstance(w, int) and isinstance(h, int) and w > 100 and h > 60:
                    try:
                        self.window.resize(w, h)
                    except Exception:
                        pass
                x = wcfg.get('x')
                y = wcfg.get('y')
                if isinstance(x, int) and isinstance(y, int):
                    try:
                        self.window.move(x, y)
                    except Exception:
                        pass
        except Exception:
            pass
        logger.debug("Apply saved geometry: %s", wcfg)
        return maximize

    def _persist_window_geometry(self) -> None:
        if not self.window:
            return
        try:
            # Update maximized flag from last known state
            maximized = bool(getattr(self, '_window_is_maximized', False))
            # If not maximized, fetch position and size
            if not maximized:
                try:
                    x, y = self.window.get_position()
                    w, h = self.window.get_size()
                    self._last_geometry = {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
                except Exception:
                    pass
            wcfg = dict(self.settings.get('window', {}) or {})
            # Merge
            if getattr(self, '_last_geometry', None):
                wcfg.update(self._last_geometry)
            wcfg['maximized'] = maximized
            self.settings['window'] = wcfg
            save_settings(self.settings)
            logger.debug("Saved window geometry: %s", wcfg)
        except Exception as e:
            logger.debug("Failed to save window geometry: %s", e)

    def _on_window_configure(self, win, event):
        # Track geometry when not maximized
        try:
            if not getattr(self, '_window_is_maximized', False):
                x, y = self.window.get_position()
                w, h = self.window.get_size()
                self._last_geometry = {'x': int(x), 'y': int(y), 'width': int(w), 'height': int(h)}
        except Exception:
            pass
        return False

    def _on_window_state(self, win, event):
        try:
            maximized = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
            self._window_is_maximized = maximized
        except Exception:
            pass
        return False

    def show_window(self):
        if self.window:
            logger.debug("Show window")
            # Apply saved geometry before showing
            try:
                maximize = self._apply_saved_geometry()
            except Exception:
                maximize = False
            # Ensure all child widgets are made visible; otherwise the window appears blank
            self.window.show_all()
            # Restore maximized state if requested
            try:
                if maximize:
                    self.window.maximize()
            except Exception:
                pass
            self.window.present_keep_focus()

    def hide_window(self):
        if self.window:
            logger.debug("Hide window")
            # Persist geometry on hide to preserve position/size
            try:
                self._persist_window_geometry()
            except Exception:
                pass
            self.window.hide()

    def toggle_window(self):
        if not self.window:
            return
        try:
            visible = bool(self.window.get_visible())
        except Exception:
            # Fallback to is_visible if available
            try:
                visible = bool(self.window.is_visible())
            except Exception:
                visible = False
        logger.debug("App hotkey toggle requested; window visible=%s", visible)
        if visible:
            logger.debug("Toggle window -> hide")
            self.hide_window()
        else:
            logger.debug("Toggle window -> show")
            self.show_window()

    def toggle_task(self, task: Task):
        # Stop any running, possibly same task
        was_running = task.is_running()
        prev = stop_all(self.roots)
        # Reset daily-goal notification flag so that a new start can notify again
        try:
            self._goal_notified_today.pop(task.id, None)
        except Exception:
            pass
        if not was_running:
            task.start()
            logger.info("Started '%s'", task.name)
            notify_show("Таймер запущен", f"{task.name}: сегодня {humanize_seconds(task.today_seconds())}")
        else:
            logger.info("Stopped '%s'", task.name)
            notify_show("Таймер остановлен", f"{task.name}: сегодня {humanize_seconds(task.today_seconds())}")
        # Check goal after each toggle (start or stop)
        self._maybe_notify_goal(task)
        self.save_all()

    def adjust_task_dialog(self, task: Task):
        logger.debug("Open adjust dialog for task '%s'", task.name)
        dialog = Gtk.Dialog(title=f"Коррекция времени для '{task.name}'", transient_for=self.window, modal=True)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Например: +5h, -30m, 1h20m")
        box = dialog.get_content_area()
        box.add(entry)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry.connect('activate', lambda *_: dialog.response(Gtk.ResponseType.OK))
        dialog.show_all()
        entry.grab_focus()
        resp = dialog.run()
        txt = entry.get_text()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                delta = parse_duration_delta(txt)
            except Exception as e:
                self._message(f"Неверный формат: {e}")
                return
            task.add_adjustment(delta)
            logger.info("Adjusted '%s' by %s", task.name, humanize_seconds(delta))
            self._maybe_notify_goal(task)
            self.save_all()
        else:
            logger.debug("Adjust dialog canceled")

    def set_goal_dialog(self, task: Task):
        logger.debug("Open daily target dialog for '%s'", task.name)
        dialog = Gtk.Dialog(title=f"Цель на день для '{task.name}'", transient_for=self.window, modal=True)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Например: 2h, 90m")
        box = dialog.get_content_area()
        box.add(entry)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.set_default_response(Gtk.ResponseType.OK)
        entry.connect('activate', lambda *_: dialog.response(Gtk.ResponseType.OK))
        dialog.show_all()
        entry.grab_focus()
        resp = dialog.run()
        txt = entry.get_text()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                sec = parse_duration_delta(txt.lstrip('+'))
                if sec < 0:
                    sec = -sec
            except Exception as e:
                self._message(f"Неверный формат: {e}")
                return
            task.daily_goal_sec = int(sec)
            logger.info("Daily target for '%s' set to %s", task.name, humanize_seconds(sec))
            self.save_all()
            # Immediately check if the goal is already met and notify
            self._maybe_notify_goal(task)
        else:
            logger.debug("Daily target dialog canceled")

    def assign_hotkey_dialog(self, task: Task):
        dialog = Gtk.Dialog(title=f"Горячая клавиша для '{task.name}'", transient_for=self.window, modal=True)
        label = Gtk.Label(label="Нажмите комбинацию клавиш... Esc — отмена, BackSpace — убрать")
        box = dialog.get_content_area()
        box.add(label)
        dialog.set_default_size(300, 80)
        dialog.connect('key-press-event', lambda w, e: self._on_hotkey_capture(dialog, task, e))
        dialog.show_all()

    def _on_hotkey_capture(self, dialog: Gtk.Dialog, task: Task, event: Gdk.EventKey):
        keyval = event.keyval
        keyname = Gdk.keyval_name(keyval) or ''
        state = event.state
        logger.debug("Hotkey capture event: key=%s state=%s", keyname, int(state))
        if keyname == 'Escape':
            logger.debug("Hotkey capture: cancel")
            dialog.destroy()
            return True
        if keyname == 'BackSpace':
            # remove hotkey
            old = self.settings['task_hotkeys'].get(task.id)
            logger.debug("Hotkey capture: clear existing '%s'", old)
            self.hotkeys.unbind(old or '')
            self.settings['task_hotkeys'].pop(task.id, None)
            self.window.set_hotkey_text(task, '')
            save_settings(self.settings)
            dialog.destroy()
            return True
        # Ignore pure modifier keys
        modifier_keys = {
            'Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R',
            'Super_L','Super_R','Meta_L','Meta_R','Hyper_L','Hyper_R',
            'ISO_Level3_Shift','Caps_Lock','Num_Lock','Scroll_Lock'
        }
        if keyname in modifier_keys:
            # keep dialog open, wait for a real key combined with modifiers
            return True
        # Build accelerator
        mods = Gtk.accelerator_get_default_mod_mask() & state
        accel = Gtk.accelerator_name(keyval, mods)
        if not accel:
            return True
        # Rebind
        old = self.settings['task_hotkeys'].get(task.id)
        if self.hotkeys.rebind(old, accel, lambda t=task: self._hotkey_toggle_task(t)):
            logger.debug("Assigned hotkey '%s' to task '%s'", accel, task.name)
            self.settings['task_hotkeys'][task.id] = accel
            self.window.set_hotkey_text(task, accel)
            save_settings(self.settings)
        else:
            logger.warning("Failed to bind hotkey '%s' for task '%s'", accel, task.name)
        dialog.destroy()
        return True

    def _hotkey_toggle_task(self, task: Task):
        self.toggle_task(task)
        # Ensure window remains hidden (do not steal focus)

    def _bind_all_hotkeys(self):
        # Per-task
        for t in self._walk(self.roots):
            accel = self.settings.get('task_hotkeys', {}).get(t.id)
            if accel:
                ok = self.hotkeys.bind(accel, lambda task=t: self._hotkey_toggle_task(task))
                if ok:
                    logger.debug("Bound task hotkey '%s' for '%s'", accel, t.name)
                else:
                    logger.warning("Failed to bind task hotkey '%s' for '%s'", accel, t.name)
        # Update texts in UI
        for t in self._walk(self.roots):
            acc = self.settings.get('task_hotkeys', {}).get(t.id, '')
            self.window.set_hotkey_text(t, acc)

    def _walk(self, lst: List[Task]):
        for t in lst:
            yield t
            yield from self._walk(t.children)

    # ---- Colors management ----
    @staticmethod
    def _is_valid_color_hex(c: Optional[str]) -> bool:
        if not isinstance(c, str):
            return False
        if len(c) != 7 or not c.startswith('#'):
            return False
        try:
            int(c[1:], 16)
            return True
        except Exception:
            return False

    def _next_color(self, used: set) -> str:
        # First pass: pick from predefined palette
        for col in self._PALETTE:
            lc = col.lower()
            if lc not in used:
                return lc
        # Fallback: generate via HSV golden ratio steps
        import colorsys
        i = len(used) + 1
        h = (i * 0.61803398875) % 1.0
        s = 0.65
        v = 0.92
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return '#%02x%02x%02x' % (int(r * 255), int(g * 255), int(b * 255))

    def _ensure_task_colors(self) -> bool:
        """Assign distinct palette colors to all tasks lacking color. Returns True if any assigned."""
        used = set()
        for t in self._walk(self.roots):
            if self._is_valid_color_hex(getattr(t, 'color', None)):
                used.add(t.color.lower())
        changed = False
        def _assign(t: Task):
            nonlocal changed, used
            if not self._is_valid_color_hex(getattr(t, 'color', None)):
                c = self._next_color(used)
                t.color = c
                used.add(c)
                changed = True
            for ch in t.children:
                _assign(ch)
        for root in self.roots:
            _assign(root)
        return changed

    def _message(self, text: str):
        md = Gtk.MessageDialog(parent=self.window, flags=0, type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK,
                               message_format=text)
        md.run()
        md.destroy()

    def open_report(self):
        try:
            open_report_flow(self.window, self.roots)
        except Exception as e:
            logger.warning("Failed to open report: %s", e)

    def _maybe_notify_goal(self, task: Task):
        if not task.daily_goal_sec:
            return
        # Use shifted day boundary (06:00 local time) as the notification key
        today_key = day_start(now()).date()
        if self._goal_notified_today.get(task.id) == today_key:
            return
        if task.today_seconds() >= task.daily_goal_sec:
            logger.info("Goal reached for '%s': today %s / target %s", task.name, humanize_seconds(task.today_seconds()), humanize_seconds(task.daily_goal_sec))
            notify_show("Цель по времени достигнута", f"{task.name}: сегодня {humanize_seconds(task.today_seconds())}")
            self._goal_notified_today[task.id] = today_key

    def _goal_check_tick(self):
        # Check running tasks every minute
        for t in self._walk(self.roots):
            if t.is_running():
                self._maybe_notify_goal(t)
        return True

    def save_all(self):
        # Ensure colors are assigned for all tasks before saving
        try:
            if self._ensure_task_colors():
                logger.debug("Assigned colors to tasks before save")
        except Exception:
            pass
        # serialize tasks
        self.data['tasks'] = [task_to_dict(t) for t in self.roots]
        logger.debug("Saving data: %d root tasks", len(self.roots))
        save_data(self.data)

    def _autosave_tick(self):
        logger.debug("Autosave tick")
        self.save_all()
        return True

    def quit(self):
        logger.debug("Quit application")
        # Save window geometry too
        try:
            self._persist_window_geometry()
        except Exception:
            pass
        self.save_all()
        try:
            if self.indicator is not None and hasattr(self.indicator, 'set_status'):
                self.indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        except Exception:
            pass
        Gtk.main_quit()


def run():
    app = App()
    app.show_window()
    Gtk.main()
    return 0
