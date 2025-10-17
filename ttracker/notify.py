import gi

gi.require_version('Notify', '0.7')
from gi.repository import Notify


def ensure_inited():
    if not Notify.is_initted():
        try:
            Notify.init("ttracker")
        except Exception:
            pass


def show(title: str, body: str):
    ensure_inited()
    try:
        n = Notify.Notification.new(title, body)
        n.show()
    except Exception:
        pass
