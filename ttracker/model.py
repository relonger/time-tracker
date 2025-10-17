import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

from .util import now, day_start, week_range, month_range, split_by_day_boundary


@dataclass
class TimeEntry:
    start: dt.datetime
    end: Optional[dt.datetime]  # None while running


@dataclass
class Adjustment:
    ts: dt.datetime
    delta_sec: int


@dataclass
class Task:
    id: str
    name: str
    color: Optional[str] = None
    children: List['Task'] = field(default_factory=list)
    daily_goal_sec: Optional[int] = None
    time_entries: List[TimeEntry] = field(default_factory=list)
    adjustments: List[Adjustment] = field(default_factory=list)

    def add_child(self, child: 'Task') -> None:
        self.children.append(child)

    def remove_child(self, child: 'Task') -> None:
        self.children.remove(child)

    def is_running(self) -> bool:
        return any(e.end is None for e in self.time_entries)

    def start(self) -> None:
        if not self.is_running():
            self.time_entries.append(TimeEntry(start=now(), end=None))

    def stop(self) -> None:
        for e in reversed(self.time_entries):
            if e.end is None:
                e.end = now()
                break

    def add_adjustment(self, delta_sec: int) -> None:
        self.adjustments.append(Adjustment(ts=now(), delta_sec=delta_sec))

    def own_seconds(self, start_ts: Optional[dt.datetime] = None, end_ts: Optional[dt.datetime] = None) -> int:
        """
        Compute time for this task only (its own time entries and adjustments),
        excluding any children contributions. Time is clipped to [start_ts, end_ts) if provided.
        """
        total = 0
        # own entries
        for e in self.time_entries:
            s = e.start
            e_end = e.end or now()
            if start_ts or end_ts:
                s_clip = s if not start_ts else max(s, start_ts)
                e_clip = e_end if not end_ts else min(e_end, end_ts)
                if e_clip > s_clip:
                    total += int((e_clip - s_clip).total_seconds())
            else:
                total += int((e_end - s).total_seconds())
        # own adjustments
        for a in self.adjustments:
            if (start_ts is None or a.ts >= start_ts) and (end_ts is None or a.ts < end_ts):
                total += a.delta_sec
        return total

    # Aggregations (includes children)
    def aggregate_seconds(self, start_ts: Optional[dt.datetime] = None, end_ts: Optional[dt.datetime] = None) -> int:
        total = 0
        # own entries
        for e in self.time_entries:
            s = e.start
            e_end = e.end or now()
            if start_ts or end_ts:
                s_clip = s if not start_ts else max(s, start_ts)
                e_clip = e_end if not end_ts else min(e_end, end_ts)
                if e_clip > s_clip:
                    total += int((e_clip - s_clip).total_seconds())
            else:
                total += int((e_end - s).total_seconds())
        # own adjustments
        for a in self.adjustments:
            if (start_ts is None or a.ts >= start_ts) and (end_ts is None or a.ts < end_ts):
                total += a.delta_sec
        # children
        for c in self.children:
            total += c.aggregate_seconds(start_ts, end_ts)
        return total

    def today_seconds(self) -> int:
        n = now()
        return self.aggregate_seconds(*_range_day(n))

    def week_seconds(self) -> int:
        n = now()
        return self.aggregate_seconds(*week_range(n))

    def month_seconds(self) -> int:
        n = now()
        return self.aggregate_seconds(*month_range(n))

    def total_seconds(self) -> int:
        return self.aggregate_seconds()


# Helpers

def _range_day(ts: dt.datetime) -> Tuple[dt.datetime, dt.datetime]:
    ds = day_start(ts)
    return ds, ds + dt.timedelta(days=1)


# Tree operations and serialization

def task_from_dict(d: Dict[str, Any]) -> Task:
    t = Task(
        id=d.get('id') or uuid.uuid4().hex,
        name=d.get('name') or "Unnamed",
        color=d.get('color'),
        daily_goal_sec=d.get('daily_goal_sec'),
        time_entries=[],
        adjustments=[],
        children=[],
    )
    for e in d.get('time_entries', []):
        s = dt.datetime.fromisoformat(e['start'])
        e_end = dt.datetime.fromisoformat(e['end']) if e.get('end') else None
        t.time_entries.append(TimeEntry(start=s, end=e_end))
    for a in d.get('adjustments', []):
        ts = dt.datetime.fromisoformat(a['ts'])
        t.adjustments.append(Adjustment(ts=ts, delta_sec=int(a['delta_sec'])))
    for ch in d.get('children', []):
        t.children.append(task_from_dict(ch))
    return t


def task_to_dict(t: Task) -> Dict[str, Any]:
    return {
        'id': t.id,
        'name': t.name,
        'color': t.color,
        'daily_goal_sec': t.daily_goal_sec,
        'time_entries': [
            {'start': e.start.isoformat(), 'end': e.end.isoformat() if e.end else None}
            for e in t.time_entries
        ],
        'adjustments': [
            {'ts': a.ts.isoformat(), 'delta_sec': int(a.delta_sec)} for a in t.adjustments
        ],
        'children': [task_to_dict(c) for c in t.children],
    }


def find_task_by_id(root_list: List[Task], task_id: str) -> Optional[Task]:
    for t in root_list:
        if t.id == task_id:
            return t
        found = find_task_by_id(t.children, task_id)
        if found:
            return found
    return None


def stop_all(root_list: List[Task]) -> Optional[Task]:
    prev = None
    for t in walk_tasks(root_list):
        if t.is_running():
            t.stop()
            prev = t
    return prev


def walk_tasks(root_list: List[Task]):
    for t in root_list:
        yield t
        yield from walk_tasks(t.children)


def move_task_within_parent(parent_list: List[Task], index: int, direction: int) -> int:
    new_index = index + direction
    if 0 <= new_index < len(parent_list):
        parent_list[index], parent_list[new_index] = parent_list[new_index], parent_list[index]
        return new_index
    return index


def new_task(name: str) -> Task:
    return Task(id=uuid.uuid4().hex, name=name)
