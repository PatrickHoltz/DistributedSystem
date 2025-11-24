import multiprocessing as mp
import socket
import json
import queue
from dataclasses import is_dataclass, asdict
from typing import Optional, Tuple


class ConnectionProcess(mp.Process):
    # * danach ist nur 1 keyword argument erlaubt
    # backlog wie viele verbindungsversuche gleichzeitig in der warteschlange sein dürfen
    def __init__(self, *,address: Tuple[str, int], backlog: int = 1, buffer_size: int = 4096):
        super().__init__(daemon=True)
        self.address = address
        self.backlog = backlog
        self.buffer_size = buffer_size

        #für jeden Typ, da ich ka habe was für typen wir schicken
        self._send_queue = mp.Queue()
        self._recv_queue = mp.Queue()

        self._stop_event = mp.Event()

    def send(self, message):
        self._send_queue.put(message)

    def get_message(self, timeout: Optional[float] = None):
        try:
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop_event.set()


    def _serialize(self, message):
        if is_dataclass(message):
           message = asdict(message)
        return json.dumps(message).encode("utf-8")

    def _deserialize(self, raw):
        return json.loads(raw.decode("utf-8"))


    def run(self):

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # damit man nach crash direkt wieder benutzenm kann
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(self.address)
        server_sock.listen(self.backlog)
        server_sock.settimeout(1.0)

        conn = None
        buffer = b""

        try:
            while not self._stop_event.is_set():
                # Warten auf verbindung
                if conn is None:
                    try:
                        conn, client_address = server_sock.accept()
                        conn.settimeout(0.1)
                        buffer = b""
                    except socket.timeout:
                        continue

                # Ausgehende nachrichten (hauptprozess) senden
                try:
                    while True:
                        msg = self._send_queue.get_nowait()
                        data = self._serialize(msg) + b"\n"
                        conn.sendall(data)
                except queue.Empty:
                    pass

                # Eingehende nachichten lsesn
                try:
                    chunk = conn.recv(self.buffer_size)
                    if not chunk:
                        conn.close()
                        conn = None
                        continue

                    buffer += chunk

                    # "\n" als trennzeichen für nachrichten
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line:
                            continue

                        try:
                            obj = self._deserialize(line)
                        except json.JSONDecodeError:
                            continue
                        # an Hauptprozess geben
                        self._recv_queue.put(obj)

                except socket.timeout:
                    #kein neues paket
                    pass

        finally:
            if conn is not None:
                conn.close()
            server_sock.close()




