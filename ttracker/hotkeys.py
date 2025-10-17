from typing import Callable, Dict, Optional
import logging

logger = logging.getLogger(__name__)

try:
    import gi
    gi.require_version('Keybinder', '3.0')
    from gi.repository import Keybinder
except Exception:  # pragma: no cover
    Keybinder = None


class GlobalHotkeys:
    def __init__(self):
        self.bound: Dict[str, Callable[[], None]] = {}
        if Keybinder is not None:
            try:
                Keybinder.init()
                logger.debug("Keybinder initialized")
            except Exception as e:
                logger.warning("Keybinder init failed: %s", e)

    def bind(self, accel: str, callback: Callable[[], None]) -> bool:
        if Keybinder is None:
            logger.warning("Keybinder not available; cannot bind '%s'", accel)
            return False
        if not accel:
            return False
        try:
            # Unbind same accel if used
            if accel in self.bound:
                Keybinder.unbind(accel)
            
            def _handler(keystr=None):
                try:
                    logger.debug("Global hotkey activated: %s", accel)
                except Exception:
                    pass
                try:
                    callback()
                except Exception as e:
                    logger.exception("Global hotkey callback failed for %s: %s", accel, e)
            
            Keybinder.bind(accel, _handler)
            self.bound[accel] = callback
            logger.debug("Global hotkey bound: %s", accel)
            return True
        except Exception as e:
            logger.warning("Global hotkey bind failed for '%s': %s", accel, e)
            return False

    def unbind(self, accel: str) -> None:
        if Keybinder is None:
            return
        try:
            Keybinder.unbind(accel)
            logger.debug("Global hotkey unbound: %s", accel)
        except Exception as e:
            logger.debug("Global hotkey unbind failed for '%s': %s", accel, e)
        self.bound.pop(accel, None)

    def rebind(self, old: Optional[str], new: Optional[str], callback: Callable[[], None]) -> bool:
        if old:
            self.unbind(old)
        if new:
            return self.bind(new, callback)
        return True
