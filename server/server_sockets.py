from __future__ import annotations

import multiprocessing as mp
import queue
import socket
import time
from threading import Thread
from typing import TYPE_CHECKING, Callable, Optional, override

from shared.data import *
from shared.packet import PacketTag, Packet
from shared.sockets import TCPConnection, SocketUtils, UDPSocket, Address, AddressedPacket
from shared.utils import Debug

if TYPE_CHECKING:
    from server_logic import ConnectionManager

class TCPListener(TCPConnection, Thread):
    """A socket which accepts incoming tcp connections if they deliver a login packet with them."""

    def __init__(self, connection_manager: ConnectionManager, stop_event):
        super().__init__(mp.Queue(), stop_event=stop_event)

        # Queue to communicate the actual port back to the parent process
        self._connection_manager = connection_manager
        self.port_queue = mp.Queue()

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("0.0.0.0", 0))

        # set a high backlog so that no login request gets lost
        self.socket.listen(50)
        self.socket.settimeout(2.0)

        self.port_queue.put(self.socket.getsockname()[1])

        while not self._stop_event.is_set():
            try:
                client_sock, client_addr = self.socket.accept()
            except socket.timeout:
                continue

            packet = SocketUtils.recv_packet(client_sock)

            if packet.tag == PacketTag.LOGIN:
                login_data = LoginData(**packet.content)
                self._connection_manager.add_connection(login_data.username, client_sock)

        Debug.log("TCP listener stopped.")


    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()


class ServerUDPSocket(Thread):
    """
    Responsible for sending UDP unicasts and broadcasts and receiving UDP unicasts.
    Use methods from the udp_socket field to send packets.
    """

    def __init__(self, connection_manager: ConnectionManager, stop_event = mp.Event()):
        super().__init__(daemon=True)
        self._connection_manager = connection_manager
        self.udp_socket: Optional[UDPSocket] = UDPSocket(stop_event)
        self.stop_event = stop_event

    def run(self):
        self.udp_socket.start()

        while not self.stop_event.is_set():
            try:
                packet, addr = self.udp_socket.recv_queue.get(timeout=2.0)
                self._handle_packet(packet, addr)
            except queue.Empty:
                continue
        Debug.log("Server udp socket stopped.")

    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.udp_socket.port_queue.get()

    def _handle_packet(self, packet: Packet, _addr: Address) -> None:
        """Handles incoming unicast packets"""
        match packet.tag:
            case PacketTag.SERVER_HEARTBEAT:
                if self._connection_manager.server_loop.is_leader:
                    server_info = ServerInfo(**packet.content)

                    #Debug.log("Server heartbeat received.", "LEADER")
                    server_state = ServerState(server_info, time.monotonic())
                    self._connection_manager.server_view[server_info.server_uuid] = server_state

            case PacketTag.BULLY_OK:
                self._connection_manager.server_loop.handle_bully_ok_message(packet)

    def send_to(self, packet: Packet, addr: Address, attempts = 1):
        for _ in range(attempts):
            self.udp_socket.send_queue.put((packet, addr))


    def broadcast(self, packet: Packet, attempts = 1, port = 10002):
        addr_packet: AddressedPacket = (packet, (SocketUtils.broadcast_ip, port))
        #Debug.log(f"broadcasting to {addr_packet[1]}")
        for _ in range(attempts):
            self.udp_socket.send_queue.put(addr_packet)


class Heartbeat(Thread):
    """
    Process to continuously send heartbeat packets in a given interval.
    If a target_ip is specified, the heartbeat is only sent to this address. Otherwise, it is broadcasted.
    """

    BROADCAST_IP = SocketUtils.broadcast_ip

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
                #Debug.log(f"sending hb to {(self.target_ip, self.port)}")
                for _ in range(self.broadcast_attempts):
                    self.socket.sendto(next_packet.encode(), (self.target_ip, self.port))

                time.sleep(self.hb_interval)
            except Exception:
                break

        self.socket.close()

    def stop(self):
        self._stop_event.set()
