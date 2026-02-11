import queue
from enum import StrEnum


class UIEventDispatcher:
    """A simple event dispatcher for Tkinter applications.
    This allows subscribing to events and emitting them in a thread-safe manner.
    Events are processed every 20ms.
    """
    def __init__(self, root):
        self._queue = queue.Queue()
        self._root = root
        self._subscribers = {}

    def subscribe(self, event_name, callback):
        self._subscribers.setdefault(event_name, []).append(callback)

    def emit(self, event_name, *args, **kwargs):
        self._queue.put((event_name, args, kwargs))

    def start(self):
        self._root.after(20, self._process)

    def _process(self):
        try:
            while True:
                event_name, args, kwargs = self._queue.get_nowait()
                for cb in self._subscribers.get(event_name, []):
                    cb(*args, **kwargs)
        except queue.Empty:
            pass

        self._root.after(20, self._process)

class Events(StrEnum):
    UPDATE_GAME_STATE = "update_game_state"
    LOGGED_IN = "logged_in"
    LOGIN_FAILED = "login_failed"
    LOGGED_OUT = "logged_out"
    LOGIN_CLICKED = "log_in_clicked"
    LOGOUT_CLICKED = "logout_clicked"
    ATTACK_CLICKED = "attack_clicked"
    NEW_MONSTER = "new_monster"
    SERVER_TIMEOUT = "server_timeout"
    SWITCH_SERVER = "switch_server"