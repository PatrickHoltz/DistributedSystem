from __future__ import annotations

import json
import multiprocessing as mp
import socket
import time
from threading import Thread
from typing import TYPE_CHECKING, Callable, Optional, override

from shared.data import *
from shared.packet import PacketTag, Packet
from shared.sockets import _TCPConnection, SocketUtils

if TYPE_CHECKING:
    from server_logic import ConnectionManager

class TCPListener(_TCPConnection, Thread):
    """A socket which accepts incoming tcp connections if they deliver a login packet with them."""

    def __init__(self, connection_manager: ConnectionManager):
        super().__init__(mp.Queue())

        # Queue to communicate the actual port back to the parent process
        self._connection_manager = connection_manager
        self.port_queue = mp.Queue()

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("0.0.0.0", 0))

        # set a high backlog so that no login request gets lost
        self.socket.listen(50)

        self.port_queue.put(self.socket.getsockname()[1])

        while True:
            client_sock, client_addr = self.socket.accept()

            packet = SocketUtils.recv_packet(client_sock)

            if packet.tag == PacketTag.LOGIN:
                login_data = LoginData(**packet.content)
                self._connection_manager.add_connection(login_data.username, client_sock)


    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()


class UDPListener(_TCPConnection, Thread):
    """A socket which accepts udp packets."""

    def __init__(self, connection_manager: ConnectionManager):
        super().__init__(mp.Queue())
        self._connection_manager = connection_manager

        # Queue to communicate the actual port back to the parent process
        self.port_queue = mp.Queue()
        self.buffer_size = 65507

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", 0))

        self.port_queue.put(self.socket.getsockname()[1])

        while True:
            try:
                data, addr = self.socket.recvfrom(self.buffer_size)
            except socket.timeout:
                # timeout
                continue
            except OSError:
                # socket was maybe closed from outside
                break

            try:
                # print("Broadcast message received from ", addr)
                packet = Packet.decode(data)
            except json.JSONDecodeError:
                # ungültiges JSON ignorieren
                continue

            # Also read server heartbeats
            if packet.tag == PacketTag.SERVER_HEARTBEAT and self._connection_manager.server_loop.is_leader:
                server_info = ServerInfo(**packet.content)

                #Debug.log("Server heartbeat received.", "LEADER")
                server_state = ServerState(server_info, time.monotonic())
                self._connection_manager.server_view[server_info.server_uuid] = server_state

    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()


class Heartbeat(Thread):
    """
    Process to continuously send heartbeat packets in a given interval.
    If a target_ip is specified, the heartbeat is only sent to this address. Otherwise, it is broadcasted.
    """

    BROADCAST_IP = SocketUtils.broadcast_ip()

    def __init__(self, packet_function: Callable[[], Packet], interval: float, target_ip: str = None, port: int = 10002, broadcast_attempts: int = 1):
        super().__init__(name="Heartbeat")
        self.packet_function = packet_function
        self.socket: Optional[socket] = None
        self.hb_interval = interval
        self.target_ip = target_ip
        self.port = port
        self.broadcast_attempts = broadcast_attempts

        self._stop_event = mp.Event()
        self._use_broadcast = target_ip is None
        self._next_hb = time.monotonic() + self.hb_interval

    def _create_socket(self):
        # udp broadcast socket
        try:
            if self._use_broadcast:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                self.target_ip = self.BROADCAST_IP
            # tcp socket
            else:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                #self.socket.connect((self.target_ip, self.port))
        except Exception:
            self.stop()

    @override
    def run(self):
        self._create_socket()

        while not self._stop_event.is_set():
            next_packet = self.packet_function()
            try:
                #print(f"sending hb to {(self.target_ip, self.port)}")
                for _ in range(self.broadcast_attempts):
                    self.socket.sendto(next_packet.encode(), (self.target_ip, self.port))

                time.sleep(self.hb_interval)
            except Exception:
                break

        self.socket.close()

    def stop(self):
        self._stop_event.set()
