import socket
import json
from threading import Thread, Event

class BroadcastListener(Thread):
    def __init__(self, port: int = 10002, on_message=None, buffer_size: int = 4096):
        super().__init__(daemon=True)

        if on_message is None:
            raise ValueError("on_message cant be null.")
        if not callable(on_message):
            raise TypeError("on_message must be callable.")

        self.port = port
        self.on_message = on_message
        self.buffer_size = buffer_size
        self._stop_event = Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        #alle interfaces
        sock.bind(("", self.port))
        #Timeout, damit recvfrom nicht unendlich blockiert
        sock.settimeout(1.0)
        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(self.buffer_size)
                except socket.timeout:
                    # oft checken, ob wir stoppen sollen
                    continue
                except OSError:
                    # Socket wurde evtl. von außen geschlossen
                    break
                try:
                    obj = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    # ungültiges JSON ignorieren
                    continue

                self.on_message(obj, addr)

        finally:
            sock.close()