from __future__ import annotations

import threading
from plyer import notification as plyer_notification  # type: ignore[import-not-found]


def notify(title: str, message: str, timeout: int = 5) -> None:
    """Send a desktop notification in a non-blocking way."""
    def _do():
        try:
            notify_func = getattr(plyer_notification, "notify", None)
            if callable(notify_func):
                notify_func(title=title, message=message, timeout=timeout, app_name="AURA")  # type: ignore[no-untyped-call]
            else:
                # Fallback to console if notify isn't callable (rare name shadowing)
                print(f"AURA: {title} - {message}")
        except Exception as e:
            # Avoid crashing on platforms where notifications are not available
            print(f"AURA notification error: {e}")

    t = threading.Thread(target=_do, daemon=True)
    t.start()
