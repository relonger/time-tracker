import gi
import datetime as dt
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

from .util import now, day_start, day_end, week_range, month_range, humanize_seconds
from .model import Task

# GTK
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

# Matplotlib (GTK3 backend)
from matplotlib.backends.backend_gtk3agg import FigureCanvasGTK3Agg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.offsetbox import AnnotationBbox, TextArea, HPacker, VPacker, DrawingArea
import numpy as np
import matplotlib.patches as mpatches


@dataclass
class ReportParams:
    start: dt.datetime
    end: dt.datetime
    aggregation: str  # 'days' | 'weeks' | 'months'


_PRESETS = [
    ("Current week", "current_week"),
    ("Current month", "current_month"),
    ("Last 7 days (incl. today)", "last_7"),
    ("Last 30 days (incl. today)", "last_30"),
    ("Current year", "current_year"),
    ("Last 365 days", "last_365"),
    ("Custom…", "custom"),
]


def _preset_range(key: str) -> Tuple[dt.datetime, dt.datetime]:
    n = now()
    if key == 'current_week':
        s, e = week_range(n)
        return s, e
    if key == 'current_month':
        s, e = month_range(n)
        return s, e
    if key == 'last_7':
        end = day_end(n)
        start = day_start(n) - dt.timedelta(days=6)
        return start, end
    if key == 'last_30':
        end = day_end(n)
        start = day_start(n) - dt.timedelta(days=29)
        return start, end
    if key == 'current_year':
        dn = day_start(n)
        s = dn.replace(month=1, day=1)
        # ensure hour is our cutoff
        if s.hour != 6:
            s = s.replace(hour=6, minute=0, second=0, microsecond=0)
        # next year start
        e = s.replace(year=s.year + 1)
        return s, e
    if key == 'last_365':
        end = day_end(n)
        start = day_start(n) - dt.timedelta(days=364)
        return start, end
    raise ValueError("Unknown preset")


def choose_params_dialog(parent: Optional[Gtk.Window]) -> Optional[ReportParams]:
    dialog = Gtk.Dialog(title="Отчет", transient_for=parent, modal=True)
    box = dialog.get_content_area()

    grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin=12)
    box.add(grid)

    # Preset
    lbl_p = Gtk.Label(label="Диапазон:")
    grid.attach(lbl_p, 0, 0, 1, 1)
    cb_preset = Gtk.ComboBoxText()
    for label, key in _PRESETS:
        cb_preset.append_text(label)
    cb_preset.set_active(2)  # default: Last 7 days
    grid.attach(cb_preset, 1, 0, 2, 1)

    # Custom dates
    lbl_s = Gtk.Label(label="Начало (YYYY-MM-DD):")
    lbl_e = Gtk.Label(label="Конец (YYYY-MM-DD):")
    entry_s = Gtk.Entry()
    entry_e = Gtk.Entry()
    entry_s.set_placeholder_text("YYYY-MM-DD")
    entry_e.set_placeholder_text("YYYY-MM-DD")
    grid.attach(lbl_s, 0, 1, 1, 1)
    grid.attach(entry_s, 1, 1, 1, 1)
    grid.attach(lbl_e, 0, 2, 1, 1)
    grid.attach(entry_e, 1, 2, 1, 1)

    # Aggregation
    lbl_a = Gtk.Label(label="Агрегация:")
    grid.attach(lbl_a, 0, 3, 1, 1)
    cb_agg = Gtk.ComboBoxText()
    for label in ("По дням", "По неделям", "По месяцам"):
        cb_agg.append_text(label)
    cb_agg.set_active(0)
    grid.attach(cb_agg, 1, 3, 1, 1)

    # Buttons
    dialog.add_button("OK", Gtk.ResponseType.OK)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.set_default_response(Gtk.ResponseType.OK)
    # Enable Enter/Esc handling globally in the dialog
    def _on_dlg_key(_w, e):
        key = Gdk.keyval_name(e.keyval) or ''
        if key in ('Return', 'KP_Enter'):
            dialog.response(Gtk.ResponseType.OK)
            return True
        if key == 'Escape':
            dialog.response(Gtk.ResponseType.CANCEL)
            return True
        return False
    dialog.connect('key-press-event', _on_dlg_key)

    # Sensitivity toggle for custom
    def _update_custom(*_):
        custom = (cb_preset.get_active_text() == "Custom…")
        for w in (lbl_s, lbl_e, entry_s, entry_e):
            w.set_sensitive(custom)
    _update_custom()
    cb_preset.connect('changed', _update_custom)

    def _activate_ok(_entry):
        dialog.response(Gtk.ResponseType.OK)
    entry_s.connect('activate', _activate_ok)
    entry_e.connect('activate', _activate_ok)

    dialog.show_all()
    resp = dialog.run()
    params = None
    if resp == Gtk.ResponseType.OK:
        # Aggregation
        agg_map = {0: 'days', 1: 'weeks', 2: 'months'}
        aggregation = agg_map.get(cb_agg.get_active(), 'days')
        # Range
        preset = [k for _, k in _PRESETS][cb_preset.get_active() or 0]
        if preset == 'custom':
            try:
                s_text = entry_s.get_text().strip()
                e_text = entry_e.get_text().strip()
                s_date = dt.date.fromisoformat(s_text)
                e_date = dt.date.fromisoformat(e_text)
                # convert to 06:00 boundaries
                s_dt = day_start(dt.datetime.combine(s_date, dt.time(0, 0)).astimezone())
                e_dt = day_end(dt.datetime.combine(e_date, dt.time(0, 0)).astimezone())
                if e_dt <= s_dt:
                    raise ValueError("End must be after start")
                params = ReportParams(start=s_dt, end=e_dt, aggregation=aggregation)
            except Exception:
                params = None
        else:
            s, e = _preset_range(preset)
            params = ReportParams(start=s, end=e, aggregation=aggregation)
    dialog.destroy()
    return params


def build_bins(start: dt.datetime, end: dt.datetime, aggregation: str) -> List[Tuple[dt.datetime, dt.datetime, str]]:
    bins: List[Tuple[dt.datetime, dt.datetime, str]] = []
    cur = start
    if aggregation == 'days':
        while cur < end:
            ds = day_start(cur)
            de = ds + dt.timedelta(days=1)
            bins.append((ds, min(de, end), ds.strftime('%Y-%m-%d')))
            cur = de
    elif aggregation == 'weeks':
        while cur < end:
            ws, we = week_range(cur)
            bins.append((ws, min(we, end), f"{ws.strftime('%Y-%m-%d')}..{(min(we, end)-dt.timedelta(seconds=1)).strftime('%Y-%m-%d')}"))
            cur = we
    elif aggregation == 'months':
        while cur < end:
            ms, me = month_range(cur)
            bins.append((ms, min(me, end), ms.strftime('%Y-%m')))
            cur = me
    else:
        raise ValueError('Unknown aggregation')
    return bins


def compute_breakdown(roots: List[Task], bins: List[Tuple[dt.datetime, dt.datetime, str]]):
    """
    Returns list aligned with bins. For each bin: dict root_task -> dict(part_name -> seconds)
    part_name is child.name for direct children (inclusive of their subtree), plus '__other__' for root's own time.
    """
    result: List[Dict[str, Dict[str, int]]] = []
    for (s, e, _label) in bins:
        per_root: Dict[str, Dict[str, int]] = {}
        for root in roots:
            parts: Dict[str, int] = {}
            # children inclusive
            for ch in root.children:
                sec = ch.aggregate_seconds(s, e)
                if sec:
                    parts[ch.name] = sec
            # other = own-only
            own = root.own_seconds(s, e)
            if own:
                parts['other'] = own
            per_root[root.name] = parts
        result.append(per_root)
    return result


def _hex_to_rgb01(hex_color: str) -> Tuple[float, float, float]:
    hex_color = (hex_color or '').strip()
    if len(hex_color) == 7 and hex_color.startswith('#'):
        r = int(hex_color[1:3], 16) / 255.0
        g = int(hex_color[3:5], 16) / 255.0
        b = int(hex_color[5:7], 16) / 255.0
        return (r, g, b)
    # fallback blue-ish
    return (0.2, 0.5, 0.9)


def build_color_lookup(roots: List[Task]) -> Dict[Tuple[str, str], Tuple[float, float, float]]:
    """Create a mapping (root.name, child.name) -> RGB tuple from stored Task.colors.
    Only direct children are mapped. 'other' is not included here (handled separately).
    """
    m: Dict[Tuple[str, str], Tuple[float, float, float]] = {}
    for r in roots:
        for ch in r.children:
            m[(r.name, ch.name)] = _hex_to_rgb01(getattr(ch, 'color', None))
    return m


def show_chart_window(parent: Optional[Gtk.Window], roots: List[Task], bins, breakdown, aggregation: str):
    win = Gtk.Window(title="TTracker Report")
    win.set_transient_for(parent)
    win.set_default_size(1100, 650)

    fig = Figure(figsize=(10.5, 5.5), dpi=100)
    ax = fig.add_subplot(111)

    # Precompute colors from stored task palette
    color_lookup = build_color_lookup(roots)

    labels = [label for (_, _, label) in bins]
    x = np.arange(len(labels))
    n_roots = max(1, len(roots))
    width = 0.8 / n_roots
    # Collect rectangles for hover tooltips: list of (rect, root_index, bin_index)
    bars_meta: List[Tuple[object, int, int]] = []
    bars_for_labels: List[Tuple[np.ndarray, np.ndarray, str]] = []

    # Draw grouped stacked bars (no hatching; colors by root/subtask)
    totals_per_root = [np.zeros(len(labels)) for _ in roots]
    # Accumulate per-root totals across the whole selected period for legend values
    totals_sec_per_root_part: Dict[str, Dict[str, int]] = {r.name: {} for r in roots}
    for ri, root in enumerate(roots):
        offsets = x - 0.4 + width/2 + ri * width
        bottoms = np.zeros(len(labels))
        # For consistent stacking order, use sorted part names present anywhere for this root
        part_names = sorted({p for b in breakdown for p in b.get(root.name, {}).keys()})
        for pn in part_names:
            vals = []
            total_sec = 0
            for bi, _ in enumerate(labels):
                sec = breakdown[bi].get(root.name, {}).get(pn, 0)
                total_sec += sec
                vals.append(sec / 3600.0)
            if pn == 'other':
                color = (0.7, 0.7, 0.7)
            else:
                color = color_lookup.get((root.name, pn), (0.2, 0.5, 0.9))
            rects = ax.bar(offsets, vals, width, bottom=bottoms, color=color, edgecolor='black', linewidth=0.2)
            # Track rectangles for hover tooltips per (day, root)
            for bi, rect in enumerate(rects):
                bars_meta.append((rect, ri, bi))
            bottoms += np.array(vals)
            if total_sec > 0:
                totals_sec_per_root_part[root.name][pn] = totals_sec_per_root_part[root.name].get(pn, 0) + total_sec
        totals_per_root[ri] = bottoms.copy()
        bars_for_labels.append((offsets, totals_per_root[ri].copy(), root.name))

    # Ensure headroom on Y-axis so labels fit within the chart and then draw labels
    try:
        ymax = max((float(np.max(tot)) for _, tot, _ in bars_for_labels), default=0.0)
    except Exception:
        ymax = 0.0
    if ymax > 0.0:
        y_range = ymax
        headroom = max(0.18 * y_range, 1.0)
        try:
            cur_bottom, _ = ax.get_ylim()
        except Exception:
            cur_bottom = 0.0
        ax.set_ylim(cur_bottom, ymax + headroom)
        pad_label = min(max(0.25 * headroom, 0.12), headroom * 0.85)
    else:
        pad_label = 0.2
    for offsets, totals, root_name in bars_for_labels:
        for xi, total in zip(offsets, totals):
            if total > 0:
                ax.text(xi, total + pad_label, root_name, rotation=90, ha='center', va='bottom', fontsize=8, alpha=0.9)

    ax.set_ylabel('Hours')
    ax.set_title({
        'days': 'По дням',
        'weeks': 'По неделям',
        'months': 'По месяцам',
    }.get(aggregation, ''))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.grid(True, axis='y', linestyle='--', alpha=0.3)

    # Reserve wider right margin for hierarchical legend/metrics
    fig.tight_layout(rect=[0.0, 0.0, 0.70, 1.0])

    # Build hierarchical legend panel with colored squares and metrics (no mathtext)
    # Precompute per-root/per-part per-bin seconds
    bins_count = len(labels)
    stats_per_root_part: Dict[str, Dict[str, List[int]]] = {r.name: {} for r in roots}
    for root in roots:
        part_names = sorted({p for b in breakdown for p in b.get(root.name, {}).keys()})
        for pn in part_names:
            per_bin = [breakdown[bi].get(root.name, {}).get(pn, 0) for bi in range(len(labels))]
            stats_per_root_part[root.name][pn] = per_bin

    # Count lines to layout
    header_gap_weight = 1.35  # spacing after root headers
    header_pre_gap_weight = 0.65  # spacing before root headers
    total_lines = 0.0
    for root in roots:
        # account for both pre-gap and post-header gap plus subtask lines
        total_lines += header_pre_gap_weight + header_gap_weight
        total_lines += len(stats_per_root_part.get(root.name, {}))
    if total_lines == 0:
        total_lines = 1.0

    # Create a side axes for the hierarchical legend
    legend_ax = fig.add_axes([0.72, 0.05, 0.27, 0.90])
    legend_ax.set_axis_off()
    # Overlay axes for tooltips (drawn last, above everything)
    overlay_ax = fig.add_axes([0, 0, 1, 1])
    overlay_ax.set_axis_off()
    overlay_ax.set_zorder(10000)

    # Layout parameters (axes fraction)
    y = 0.98
    raw_line_h = 0.9 / max(1, total_lines)
    # Cap maximum spacing to keep legend compact when there are few lines
    line_h = min(0.035, raw_line_h)
    rect_w = 0.06

    for root in roots:
        # Root totals per bin
        root_per_bin = [sum(breakdown[bi].get(root.name, {}).values()) for bi in range(len(labels))]
        total = float(sum(root_per_bin))
        avg = total / max(1, bins_count)
        min_v = float(min(root_per_bin)) if root_per_bin else 0.0
        max_v = float(max(root_per_bin)) if root_per_bin else 0.0
        header = f"{root.name}: {humanize_seconds(int(total))} (avg. {avg/3600.0:.1f}h, [{min_v/3600.0:.1f}h - {max_v/3600.0:.1f}h])"
        # Use center alignment for header to keep spacing consistent with subtask rows
        y -= line_h * header_pre_gap_weight
        legend_ax.text(0.0, y, header, fontsize=9, fontweight='bold', va='center', transform=legend_ax.transAxes)
        y -= line_h * header_gap_weight
        # Subtasks sorted by total desc
        items = []
        for pn, per_bin in stats_per_root_part.get(root.name, {}).items():
            s = float(sum(per_bin))
            items.append((pn, per_bin, s))
        items.sort(key=lambda t: t[2], reverse=True)
        for pn, per_bin, s in items:
            avg_p = s / max(1, bins_count)
            min_p = float(min(per_bin)) if per_bin else 0.0
            max_p = float(max(per_bin)) if per_bin else 0.0
            title = 'прочее' if pn == 'other' else pn
            col = (0.7, 0.7, 0.7) if pn == 'other' else color_lookup.get((root.name, pn), (0.2, 0.5, 0.9))
            # Make legend color bars pleasantly thick: ~60% of line height, with safe min/max caps
            rect_h = min(max(0.010, line_h * 0.60), 0.028)
            # Colored bar (horizontal)
            legend_ax.add_patch(mpatches.FancyBboxPatch((0.0, y - rect_h/2), rect_w, rect_h,
                                                        boxstyle="square,pad=0.0",
                                                        facecolor=col, edgecolor='black', linewidth=0.2,
                                                        transform=legend_ax.transAxes))
            text = f" {title} — {humanize_seconds(int(s))} (avg. {avg_p/3600.0:.1f}h, [{min_p/3600.0:.1f}h - {max_p/3600.0:.1f}h])"
            legend_ax.text(rect_w + 0.01, y, text, fontsize=9, va='center', transform=legend_ax.transAxes)
            y -= line_h

    # Hover tooltip per bar implemented as AnnotationBbox on a top overlay axes
    tooltip_ab = None  # type: ignore

    def _build_tooltip_box(ri: int, bi: int):
        nonlocal tooltip_ab
        root = roots[ri]
        parts = breakdown[bi].get(root.name, {})
        day_label = labels[bi]
        total_sec = int(sum(parts.values()))
        # Header line
        header = TextArea(f"{day_label} — {root.name}: {humanize_seconds(total_sec)}", textprops=dict(size=9, color='black'))
        # Subtask rows
        rows = [header]
        for pn, sec in sorted(parts.items(), key=lambda kv: kv[1], reverse=True):
            if sec <= 0:
                continue
            title = 'прочее' if pn == 'other' else pn
            col = (0.7, 0.7, 0.7) if pn == 'other' else color_lookup.get((root.name, pn), (0.2, 0.5, 0.9))
            da = DrawingArea(10, 10, 0, 0)
            da.add_artist(mpatches.Rectangle((0, 0), 12, 7, facecolor=col, edgecolor='#222222', linewidth=0.5))
            label = TextArea(f" {title} — {humanize_seconds(int(sec))}", textprops=dict(size=9, color='black'))
            rows.append(HPacker(children=[da, label], align='baseline', pad=0, sep=2))
        content = VPacker(children=rows, align='left', pad=2, sep=2)
        # Create annotation; position is set later to mouse location in figure fraction
        tooltip_ab = AnnotationBbox(content, (0, 0),
                                    xybox=(14, 14), boxcoords=("offset points"),
                                    xycoords='axes fraction',
                                    frameon=True,
                                    bboxprops=dict(fc="#ffffff", ec="#222222", lw=0.9, alpha=1.0),
                                    box_alignment=(0, 0),
                                    zorder=100000)
        return tooltip_ab

    def _show_tooltip(ev, ri: int, bi: int):
        nonlocal tooltip_ab
        # Remove old annotation box
        if tooltip_ab is not None:
            try:
                tooltip_ab.remove()
            except Exception:
                pass
            tooltip_ab = None
        ab = _build_tooltip_box(ri, bi)
        # Position near the mouse: use figure-fraction coords and put box to the top-right of cursor
        try:
            xf, yf = fig.transFigure.inverted().transform((ev.x, ev.y))
            # Clamp inside figure bounds with small margins
            xf = min(max(xf, 0.02), 0.98)
            yf = min(max(yf, 0.02), 0.98)
            ab.xy = (xf, yf)
        except Exception:
            ab.xy = (0.02, 0.98)
        overlay_ax.add_artist(ab)

    canvas = FigureCanvas(fig)

    def _on_move(event):
        nonlocal tooltip_ab
        # Show per-day legend with data when hovering a bar (any stacked segment)
        if event.inaxes not in (ax, overlay_ax):
            if tooltip_ab is not None:
                try:
                    tooltip_ab.remove()
                except Exception:
                    pass
                tooltip_ab = None
                canvas.draw_idle()
            return
        for rect, ri, bi in bars_meta:
            contains, _ = rect.contains(event)
            if contains:
                _show_tooltip(event, ri, bi)
                canvas.draw_idle()
                return
        if tooltip_ab is not None:
            try:
                tooltip_ab.remove()
            except Exception:
                pass
            tooltip_ab = None
            canvas.draw_idle()

    canvas.mpl_connect("motion_notify_event", _on_move)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.set_spacing(0)
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    box.set_margin_start(6)
    box.set_margin_end(6)
    box.pack_start(canvas, True, True, 0)

    win.add(box)
    
    # Allow closing the report window with ESC
    def _on_win_key(_w, e):
        key = Gdk.keyval_name(e.keyval) or ''
        if key == 'Escape':
            win.destroy()
            return True
        return False
    win.connect('key-press-event', _on_win_key)

    win.show_all()
    return win


def open_report_flow(parent: Optional[Gtk.Window], roots: List[Task]):
    params = choose_params_dialog(parent)
    if not params:
        return
    bins = build_bins(params.start, params.end, params.aggregation)
    bd = compute_breakdown(roots, bins)
    show_chart_window(parent, roots, bins, bd, params.aggregation)
