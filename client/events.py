class Event:
    def __init__(self):
        self._subscribers = []

    def subscribe(self, callback):
        """Register a function to be called when event is triggered."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback):
        """Remove a subscribed function."""
        self._subscribers.remove(callback)

    def trigger(self, *args, **kwargs):
        """Call all subscribers with given arguments."""
        for callback in self._subscribers:
            callback(*args, **kwargs)

UPDATE_GAME_STATE = Event()
ON_LOGGED_IN = Event()
ON_LOGGED_OUT = Event()
