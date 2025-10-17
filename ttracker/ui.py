import gi
import logging

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
# Try to import X11 helpers for accurate focus timestamps (Xorg only)
try:
    gi.require_version('GdkX11', '3.0')
    from gi.repository import GdkX11  # type: ignore
except Exception:  # pragma: no cover
    GdkX11 = None  # type: ignore
from typing import List, Optional, Callable, Tuple

from .model import Task, new_task, move_task_within_parent
from .util import humanize_seconds

logger = logging.getLogger(__name__)


# TreeStore columns
COL_TASK_OBJ = 0
COL_ID = 1
COL_NAME = 2
COL_RUNNING = 3
COL_ICON = 4
COL_TODAY = 5
COL_WEEK = 6
COL_MONTH = 7
COL_TOTAL = 8
COL_GOAL = 9
COL_HOTKEY = 10
COL_DOT = 11


class TTrackerWindow(Gtk.Window):
    def __init__(self, roots: List[Task],
                 on_toggle_task: Callable[[Task], None],
                 on_adjust_task: Callable[[Task], None],
                 on_set_goal: Callable[[Task], None],
                 on_set_hotkey: Callable[[Task], None],
                 on_show_report: Callable[[], None],
                 on_save: Callable[[], None],
                 hotkey_lookup: Optional[Callable[[Task], str]] = None):
        super().__init__(title="TTracker")
        self.set_default_size(900, 500)
        self.roots = roots
        self.on_toggle_task = on_toggle_task
        self.on_adjust_task = on_adjust_task
        self.on_set_goal = on_set_goal
        self.on_set_hotkey = on_set_hotkey
        self.on_show_report = on_show_report
        self.on_save = on_save
        self.hotkey_lookup = hotkey_lookup

        # Layout: top bar with Report button + tree below
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        topbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        topbar.set_spacing(6)
        topbar.set_margin_top(6)
        topbar.set_margin_start(6)
        topbar.set_margin_end(6)
        vbox.pack_start(topbar, False, False, 0)

        btn_report = Gtk.Button.new_with_label("Отчет (R)")
        btn_report.connect('clicked', lambda *_: self.on_show_report())
        topbar.pack_start(btn_report, False, False, 0)

        self.store = Gtk.TreeStore(object, str, str, bool, str, str, str, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        # Ensure the tree can accept focus and will be focus target on show
        try:
            self.tree.set_can_focus(True)
            self.set_accept_focus(True)
            self.set_focus(self.tree)
        except Exception:
            pass
        self.tree.connect('row-activated', self._on_row_activated)
        self.tree.connect('key-press-event', self._on_key_press)
        self.tree.connect('button-press-event', self._on_button_press)

        # Columns
        self._editing_name_path: Optional[str] = None
        self.name_tree_column = self._append_text_column("Task", COL_NAME, editable=True, edit_cb=self._on_name_edited)
        self.icon_tree_column = self._append_status_column("")
        self._append_text_column("Today", COL_TODAY)
        self._append_text_column("Week", COL_WEEK)
        self._append_text_column("Month", COL_MONTH)
        self._append_text_column("Total", COL_TOTAL)
        self._append_text_column("Daily target", COL_GOAL)
        self.hotkey_tree_column = self._append_text_column("Hotkey", COL_HOTKEY)

        scroll = Gtk.ScrolledWindow()
        scroll.add(self.tree)
        vbox.pack_start(scroll, True, True, 0)

        self._rebuild_store()

        # periodic refresh
        GLib.timeout_add_seconds(1, self._tick_update)

    def _on_button_press(self, widget, event):
        x = int(event.x)
        y = int(event.y)
        res = self.tree.get_path_at_pos(x, y)
        if res is None:
            return False
        path, column, cell_x, cell_y = res
        it = self.store.get_iter(path) if path else None
        # Single left-click on status column -> toggle timer
        if event.type == Gdk.EventType.BUTTON_PRESS and getattr(event, 'button', 1) == 1:
            if it and column == getattr(self, 'icon_tree_column', None):
                task: Task = self.store.get_value(it, COL_TASK_OBJ)
                logger.debug("Single-click status icon -> toggle task '%s'", task.name)
                self.on_toggle_task(task)
                self.on_save()
                self._refresh_rows()
                return True
        # Double-click on Hotkey column -> assign hotkey
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if it and column == getattr(self, 'hotkey_tree_column', None):
                task: Task = self.store.get_value(it, COL_TASK_OBJ)
                logger.debug("Double-click hotkey column for task '%s'", task.name)
                self.on_set_hotkey(task)
                self.on_save()
                self._refresh_rows()
                return True
            # Double-click on Name column -> start editing
            if column == getattr(self, 'name_tree_column', None):
                logger.debug("Double-click name column; start editing")
                self.tree.set_cursor(path, self.name_tree_column, start_editing=True)
                return True
        return False

    def present_keep_focus(self):
        try:
            logger.debug("present_keep_focus: start")
            # Ensure window is visible
            if not self.get_visible():
                self.show_all()
            # Deiconify if minimized
            try:
                self.deiconify()
            except Exception:
                pass
            # Compute a reliable timestamp for focus (prefer current event, else X11 server time)
            ts = 0
            try:
                ts = Gtk.get_current_event_time() or 0
            except Exception:
                ts = 0
            if not ts:
                try:
                    gdk_win_probe = self.get_window()
                    if GdkX11 is not None and gdk_win_probe is not None:
                        ts = int(GdkX11.x11_get_server_time(gdk_win_probe))
                        logger.debug("present_keep_focus: obtained X11 server time: %s", ts)
                except Exception as e:
                    logger.debug("present_keep_focus: x11_get_server_time failed: %s", e)
            # Present with best timestamp available
            try:
                self.present_with_time(ts or Gdk.CURRENT_TIME)
            except Exception:
                try:
                    self.present()
                except Exception:
                    pass
            # Prefer focusing the tree and ensure there is a selection
            try:
                self.set_focus(self.tree)
            except Exception:
                pass
            # Raise to front once the Gdk window is available; also try direct Gdk focus with timestamp
            def _raise_later():
                try:
                    gdk_win = self.get_window()
                    if gdk_win:
                        try:
                            gdk_win.raise_()
                        except Exception:
                            pass
                        # If we have a timestamp, ask GDK to focus the window explicitly
                        try:
                            if ts:
                                gdk_win.focus(ts)
                                logger.debug("present_keep_focus: gdk window focus(ts=%s) called", ts)
                        except Exception as e:
                            logger.debug("present_keep_focus: gdk window focus failed: %s", e)
                    # Briefly keep the window above, then revert
                    try:
                        self.set_keep_above(True)
                    except Exception:
                        pass
                    def _unset_above():
                        try:
                            self.set_keep_above(False)
                        except Exception:
                            pass
                        return False
                    GLib.timeout_add(300, _unset_above)
                except Exception:
                    pass
                # Ensure selection and focus in tree
                try:
                    self._ensure_focus_and_selection()
                except Exception:
                    try:
                        self.tree.grab_focus()
                    except Exception:
                        pass
                # Explicitly activate focus if supported
                try:
                    self.activate_focus()
                except Exception:
                    pass
                logger.debug("present_keep_focus: focused tree and activated focus (ts=%s)", ts)
                return False
            GLib.idle_add(_raise_later)
        except Exception:
            # Fallback
            try:
                self.present()
            except Exception:
                pass
            GLib.idle_add(lambda: (self._ensure_focus_and_selection(), False))

    def _ensure_focus_and_selection(self):
        try:
            sel = self.tree.get_selection()
            model, it = sel.get_selected()
            if it is None:
                it = self.store.get_iter_first()
                if it is not None:
                    path = self.store.get_path(it)
                    try:
                        self.tree.expand_to_path(path)
                    except Exception:
                        pass
                    try:
                        sel.select_path(path)
                    except Exception:
                        pass
                    try:
                        self.tree.set_cursor(path, None, False)
                    except Exception:
                        pass
            try:
                self.tree.grab_focus()
            except Exception:
                pass
            logger.debug("_ensure_focus_and_selection: selection ensured and focus grabbed")
        except Exception:
            pass

    def _append_text_column(self, title: str, col_index: int, editable: bool = False, edit_cb: Optional[Callable] = None):
        renderer = Gtk.CellRendererText()
        renderer.set_property('editable', editable)
        if editable and edit_cb is not None:
            renderer.connect('edited', edit_cb)
        column = Gtk.TreeViewColumn(title, renderer, text=col_index)
        column.set_resizable(True)
        self.tree.append_column(column)
        # Special handling for name column: track editing to prevent focus loss
        if col_index == COL_NAME and editable:
            self._name_renderer = renderer
            try:
                renderer.connect('editing-started', self._on_name_editing_started)
                renderer.connect('editing-canceled', self._on_name_editing_canceled)
            except Exception as e:
                logger.debug("Failed to connect name editing signals: %s", e)
        return column

    def _append_pixbuf_column(self, title: str, col_index: int):
        # Use icon-name render
        renderer = Gtk.CellRendererPixbuf()
        column = Gtk.TreeViewColumn(title, renderer, icon_name=col_index)
        column.set_resizable(False)
        self.tree.append_column(column)
        return column

    def _append_status_column(self, title: str):
        # Composite column: clock icon when running (COL_ICON), centered dot when idle (COL_DOT)
        column = Gtk.TreeViewColumn(title)
        icon_renderer = Gtk.CellRendererPixbuf()
        dot_renderer = Gtk.CellRendererText()
        try:
            dot_renderer.set_property('xalign', 0.5)
            dot_renderer.set_property('yalign', 0.5)
        except Exception:
            pass
        column.pack_start(icon_renderer, True)
        column.add_attribute(icon_renderer, 'icon_name', COL_ICON)
        column.pack_start(dot_renderer, True)
        column.add_attribute(dot_renderer, 'text', COL_DOT)
        column.set_resizable(False)
        self.tree.append_column(column)
        return column

    # Editing state handlers for name column
    def _on_name_editing_started(self, renderer, editable, path_str: str):
        logger.debug("Name editing started at path %s", path_str)
        self._editing_name_path = path_str

    def _on_name_editing_canceled(self, renderer):
        logger.debug("Name editing canceled")
        self._editing_name_path = None

    def _rebuild_store(self):
        self.store.clear()
        for t in self.roots:
            self._add_task_to_store(None, t)
        # Expand all rows by default so the whole tree is visible
        try:
            self.tree.expand_all()
        except Exception:
            pass
        self._refresh_rows()

    def _add_task_to_store(self, parent_iter: Optional[Gtk.TreeIter], task: Task) -> Gtk.TreeIter:
        running = task.is_running()
        icon_name = 'alarm-symbolic' if running else ''
        dot_text = '' if running else '•'
        # Determine current hotkey text using provided lookup (if any)
        hotkey_text = self.hotkey_lookup(task) if getattr(self, 'hotkey_lookup', None) else ''
        it = self.store.append(parent_iter, [
            task,                 # COL_TASK_OBJ
            task.id,              # COL_ID
            task.name,            # COL_NAME
            running,              # COL_RUNNING
            icon_name,            # COL_ICON
            '',                   # COL_TODAY (filled on refresh)
            '',                   # COL_WEEK
            '',                   # COL_MONTH
            '',                   # COL_TOTAL
            self._goal_text(task),# COL_GOAL
            hotkey_text,          # COL_HOTKEY
            dot_text,             # COL_DOT
        ])
        for c in task.children:
            self._add_task_to_store(it, c)
        return it

    def _goal_text(self, t: Task) -> str:
        return humanize_seconds(t.daily_goal_sec) if t.daily_goal_sec else ''

    def _refresh_rows(self) -> None:
        def walk(it: Optional[Gtk.TreeIter]):
            while it is not None:
                task = self.store.get_value(it, COL_TASK_OBJ)
                # Avoid touching the name column while it's being edited to preserve focus
                try:
                    cur_path = self.store.get_path(it).to_string()
                except Exception:
                    cur_path = None
                # If the name cell is being edited, skip updating this entire row to preserve editor focus
                try:
                    if self._editing_name_path and cur_path == self._editing_name_path:
                        # still refresh children
                        child = self.store.iter_children(it)
                        if child:
                            walk(child)
                        it = self.store.iter_next(it)
                        continue
                except Exception:
                    pass
                # Update row normally
                self.store.set_value(it, COL_NAME, task.name)
                running = task.is_running()
                self.store.set_value(it, COL_RUNNING, running)
                # running -> clock icon, no dot; idle -> no icon, centered dot
                self.store.set_value(it, COL_ICON, 'alarm-symbolic' if running else '')
                self.store.set_value(it, COL_DOT, '' if running else '•')
                self.store.set_value(it, COL_TODAY, humanize_seconds(task.today_seconds()))
                self.store.set_value(it, COL_WEEK, humanize_seconds(task.week_seconds()))
                self.store.set_value(it, COL_MONTH, humanize_seconds(task.month_seconds()))
                self.store.set_value(it, COL_TOTAL, humanize_seconds(task.total_seconds()))
                self.store.set_value(it, COL_GOAL, self._goal_text(task))
                # Update HOTKEY text using lookup if provided
                try:
                    if getattr(self, 'hotkey_lookup', None):
                        self.store.set_value(it, COL_HOTKEY, self.hotkey_lookup(task))
                except Exception:
                    pass
                child = self.store.iter_children(it)
                if child:
                    walk(child)
                it = self.store.iter_next(it)
        root = self.store.get_iter_first()
        walk(root)

    def _tick_update(self):
        self._refresh_rows()
        return True

    def _on_row_activated(self, tree: Gtk.TreeView, path: Gtk.TreePath, column: Gtk.TreeViewColumn):
        it = self.store.get_iter(path)
        if not it:
            return
        # Do not toggle on double-click; only allow editing hotkeys/name as below
        # If activated on the name column, start editing
        if column == getattr(self, 'name_tree_column', None):
            self.tree.set_cursor(path, self.name_tree_column, start_editing=True)
            return
        # For other columns do nothing
        return

    def _on_name_edited(self, renderer, path_str: str, new_text: str):
        it = self.store.get_iter_from_string(path_str)
        if it:
            task: Task = self.store.get_value(it, COL_TASK_OBJ)
            new_name = new_text.strip()
            if new_name:
                logger.debug("Rename task '%s' -> '%s'", task.name, new_name)
                task.name = new_name
            else:
                logger.debug("Empty name entered; keep original '%s'", task.name)
            self._editing_name_path = None
            self.on_save()
            self._refresh_rows()

    def _on_key_press(self, widget, event: Gdk.EventKey):
        keyname = Gdk.keyval_name(event.keyval) or ''
        state = event.state
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        alt = state & Gdk.ModifierType.MOD1_MASK
        # Handle keys
        if keyname == 'space':
            logger.debug("Space pressed -> toggle selected task")
            self._activate_selected()
            return True
        if keyname == 'Return':
            logger.debug("Enter pressed -> adjust selected task")
            self._adjust_selected()
            return True
        if keyname in ('T', 't'):
            logger.debug("T pressed -> set daily target for selected task")
            self._set_goal_selected()
            return True
        if keyname in ('R', 'r'):
            # Open report dialog
            try:
                self.on_show_report()
            except Exception:
                pass
            return True
        if alt and keyname == 'Insert':
            logger.debug("Alt+Insert -> add root task")
            self._add_root_task()
            return True
        if keyname == 'Insert':
            logger.debug("Insert -> add subtask or root if none selected")
            self._add_task_selected()
            return True
        if keyname == 'Delete':
            logger.debug("Delete -> delete selected task")
            self._delete_task_selected()
            return True
        if ctrl and keyname in ('Up', 'Down'):
            logger.debug("Ctrl+%s -> move selected task", keyname)
            self._move_selected(-1 if keyname == 'Up' else 1)
            return True
        if keyname in ('Left', 'Right'):
            it = self._get_selected_iter()
            if it:
                path = self.store.get_path(it)
                if keyname == 'Left':
                    logger.debug("Left pressed -> collapse row %s", path.to_string())
                    try:
                        self.tree.collapse_row(path)
                    except Exception:
                        pass
                else:
                    logger.debug("Right pressed -> expand row %s", path.to_string())
                    try:
                        self.tree.expand_row(path, open_all=False)
                    except Exception:
                        pass
                return True
        return False

    def _get_selected_iter(self) -> Optional[Gtk.TreeIter]:
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        return it

    def _activate_selected(self):
        it = self._get_selected_iter()
        if it:
            task: Task = self.store.get_value(it, COL_TASK_OBJ)
            self.on_toggle_task(task)
            self.on_save()
            self._refresh_rows()

    def _adjust_selected(self):
        it = self._get_selected_iter()
        if not it:
            return
        task: Task = self.store.get_value(it, COL_TASK_OBJ)
        self.on_adjust_task(task)
        self.on_save()
        self._refresh_rows()

    def _set_goal_selected(self):
        it = self._get_selected_iter()
        if not it:
            return
        task: Task = self.store.get_value(it, COL_TASK_OBJ)
        self.on_set_goal(task)
        self.on_save()
        self._refresh_rows()

    def _add_task_selected(self):
        it = self._get_selected_iter()
        if it:
            parent_task: Task = self.store.get_value(it, COL_TASK_OBJ)
            child = new_task("New subtask")
            parent_task.add_child(child)
            # Add visually
            child_it = self._add_task_to_store(it, child)
            path = self.store.get_path(child_it)
            self.tree.expand_to_path(path)
            logger.debug("Added subtask under '%s'", parent_task.name)
            # Start editing name immediately and keep focus
            self.tree.set_cursor(path, self.name_tree_column, start_editing=True)
        else:
            # add root
            self._add_root_task()
            return
        self.on_save()

    def _add_root_task(self):
        child = new_task("New task")
        self.roots.append(child)
        child_it = self._add_task_to_store(None, child)
        path = self.store.get_path(child_it)
        logger.debug("Added root task")
        # Start editing name immediately and keep focus
        self.tree.set_cursor(path, self.name_tree_column, start_editing=True)
        self.on_save()

    def _delete_task_selected(self):
        it = self._get_selected_iter()
        if not it:
            return
        task: Task = self.store.get_value(it, COL_TASK_OBJ)
        dialog = Gtk.MessageDialog(self, 0, Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL,
                                   f"Delete '{task.name}'?")
        resp = dialog.run()
        dialog.destroy()
        if resp == Gtk.ResponseType.OK:
            # remove from model
            parent = self.store.iter_parent(it)
            if parent:
                parent_task: Task = self.store.get_value(parent, COL_TASK_OBJ)
                parent_task.remove_child(task)
            else:
                self.roots.remove(task)
            self.store.remove(it)
            self.on_save()

    def _move_selected(self, direction: int):
        it = self._get_selected_iter()
        if not it:
            return
        parent = self.store.iter_parent(it)
        # Determine list in model
        if parent:
            parent_task: Task = self.store.get_value(parent, COL_TASK_OBJ)
            siblings = parent_task.children
        else:
            siblings = self.roots
        task: Task = self.store.get_value(it, COL_TASK_OBJ)
        idx = siblings.index(task)
        new_idx = move_task_within_parent(siblings, idx, direction)
        if new_idx != idx:
            # rebuild store for simplicity
            self._rebuild_store()
            # restore selection
            # Find task in store
            self._select_task(task)
            self.on_save()

    def _select_task(self, task: Task):
        def find_it(it: Optional[Gtk.TreeIter]) -> Optional[Gtk.TreeIter]:
            while it is not None:
                t = self.store.get_value(it, COL_TASK_OBJ)
                if t is task:
                    return it
                # search children
                child = self.store.iter_children(it)
                if child:
                    found = find_it(child)
                    if found:
                        return found
                it = self.store.iter_next(it)
            return None
        it = find_it(self.store.get_iter_first())
        if it:
            path = self.store.get_path(it)
            self.tree.expand_to_path(path)
            self.tree.set_cursor(path, None, False)

    # Exposed controls for hotkey assignment
    def set_hotkey_text(self, task: Task, text: str):
        # Find the row and set
        def walk(it: Optional[Gtk.TreeIter]):
            while it is not None:
                t = self.store.get_value(it, COL_TASK_OBJ)
                if t is task:
                    self.store.set_value(it, COL_HOTKEY, text)
                child = self.store.iter_children(it)
                if child:
                    walk(child)
                it = self.store.iter_next(it)
        walk(self.store.get_iter_first())
