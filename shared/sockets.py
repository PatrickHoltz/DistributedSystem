"""This module contains classes for sending packets in different forms."""
import ipaddress
import json
import multiprocessing as mp
import queue
import signal
import socket
import struct
import subprocess
import time
from collections import deque
from concurrent.futures import Future
from threading import Thread
from typing import Optional, Callable, TypeVar, Type

from shared.packet import Packet
from shared.utils import Debug

type Address = tuple[str, int]
type AddressedPacket = tuple[Packet, Address]

class SocketUtils:
    T = TypeVar('T')

    @classmethod
    def recv_exact(cls, n_bytes: int, conn: socket.socket, timeout_s: float = None):
        """Receives exactly n bytes from the given connection. By default, blocks until the data is received.
        If a timeout is set and the data is not available, a timeout error is raised.
        """
        old_timeout = conn.gettimeout()
        conn.settimeout(timeout_s)
        data = b""
        while len(data) < n_bytes:
            chunk = conn.recv(n_bytes - len(data))
            if not chunk:
                conn.close()
                raise ConnectionError("connection closed")
            data += chunk
        conn.settimeout(old_timeout)
        return data

    #Gossip
    GOSSIP_PLAYER_STATS = "gossip_player_stats"
    GOSSIP_MONSTER_SYNC = "gossip_monster_sync"

    @classmethod
    def recv_packet(cls, sock: socket.socket) -> Packet:
        """Blocks until a full packet is received from the socket.
        """
        length_bytes = cls.recv_exact(4, sock)
        data_length = struct.unpack('!I', length_bytes)[0]
        json_bytes = cls.recv_exact(data_length, sock)
        packet = Packet.decode(length_bytes + json_bytes)
        return packet

    @staticmethod
    def _get_local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't need to be reachable
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()

    local_ip = _get_local_ip()

    @staticmethod
    def _get_broadcast_addr() -> str:

        cmd = [
            "wmic",
            "nicconfig",
            "where",
            "IPEnabled=true",
            "get",
            "IPAddress,IPSubnet",
            "/format:csv"
        ]

        output = subprocess.check_output(cmd, text=True)

        for line in output.splitlines():
            if "," not in line or "Node" in line:
                continue

            _, ip_list, subnet_list = line.split(",", 2)

            # IPAddress and IPSubnet are arrays like: {192.168.1.23}
            ip = ip_list.strip("{}").split(';')[0]
            mask = subnet_list.strip("{}").split(';')[0]

            iface = ipaddress.ip_interface(f"{ip}/{mask}")

            return str(iface.network.broadcast_address)
        raise "Could not find broadcast address."

    broadcast_ip = _get_broadcast_addr()

    @classmethod
    def get_typed_packet(cls, packet: Packet, content_type: Type[T]) -> Packet[T] | None:
        """
        Extracts the content of a packet and converts it to the specified type.
        Returns None if the conversion fails.
        """
        try:
            content = content_type(**packet.content)
            return Packet(content=content, tag=packet.tag)
        except TypeError:
            return None


class UDPSocket(mp.Process):
    """
    Socket to unicast and broadcast UDP packets and receive unicast packets.
    """

    def __init__(self, stop_event=None):
        super().__init__()
        self._stop_event = mp.Event() if stop_event is None else stop_event
        self.buffer_size: int = 65507

        self._sender: Optional[Thread] = None
        self._receiver: Optional[Thread] = None

        self.send_queue: mp.Queue[AddressedPacket] = mp.Queue()
        self.recv_queue: mp.Queue[AddressedPacket] = mp.Queue()

        # the latest packet uuids received
        self.latest_uuids = deque(["" for _ in range(100)])

        self.port_queue: mp.Queue[int] = mp.Queue()
        
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # let OS decide port, therefore no broadcast can be received
        self._socket.bind(("", 0))

        assigned_port = self._socket.getsockname()[1]
        self.port_queue.put(assigned_port)

    def run(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # wait until required fields are set up
        #time.sleep(1.0)

        self._sender = Thread(target=self._send_loop, daemon=True)
        self._receiver = Thread(target=self._recv_loop, daemon=True)
        self._sender.start()
        self._receiver.start()

        self._stop_event.wait()

        self._socket.shutdown(socket.SHUT_RDWR)
        self._sender.join(timeout=5)
        self._receiver.join(timeout=5)

        self._socket.close()
        Debug.log("UDP socket stopped.")

    def _send_loop(self):
        while not self._stop_event.is_set():
            try:
                packet, addr = self.send_queue.get(timeout=2.0)
                self._socket.sendto(packet.encode(), addr)
            except queue.Empty:
                continue
            except OSError as e:
                Debug.log(f"Could not send package. {str(e)}")
                continue
        Debug.log("Sender stopped.")

    def _recv_loop(self):
        while not self._stop_event.is_set():
            try:
                data, addr = self._socket.recvfrom(self.buffer_size)
                packet = Packet.decode(data)

                # if packet uuid has been seen before lately, ignore
                if packet.packet_uuid in self.latest_uuids:
                    continue
                else:
                    self.latest_uuids.appendleft(packet.packet_uuid)
                    self.latest_uuids.pop()

                self.recv_queue.put((packet, addr))
            except socket.timeout:
                continue
            except OSError:
                break
        Debug.log("Receiver stopped.")

    def stop(self):
        self._stop_event.set()


class BroadcastSocket(Thread):
    """Broadcasts a single packet to the specified broadcast address using a new socket. A response can be obtained."""

    BROADCAST_IP = SocketUtils.broadcast_ip

    def __init__(
            self,
            packet: Packet,
            response_handler: Callable[[Packet, tuple[str, int]], None] = None,
            response_timeout_handler: Callable = None,
            broadcast_port: int = 10002,
            timeout_s: float = 10.0,
            send_attempts: int = 1
    ):
        super().__init__()
        self.timeout_s = timeout_s
        self.send_packet = packet
        self.broadcast_port = broadcast_port
        self.broadcast_address = self.BROADCAST_IP
        self.future: Future[Optional[Packet]] = Future()
        self.response_handler = response_handler
        self.response_timeout_handler = response_timeout_handler
        self.send_attempts = send_attempts

    def run(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.settimeout(self.timeout_s)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            for i in range(self.send_attempts):
                udp_socket.sendto(self.send_packet.encode(), (self.broadcast_address, self.broadcast_port))

            try:
                data, addr = udp_socket.recvfrom(4096)
                received = Packet.decode(data)

                if self.response_handler:
                    self.response_handler(received, addr)

                if not self.future.done():
                    self.future.set_result(received)

            except socket.timeout:
                if self.response_timeout_handler:
                    self.response_timeout_handler()
                if not self.future.done():
                    self.future.set_result(None)
        finally:
            udp_socket.close()


class BroadcastListener(Thread):
    """A thread that listens to incoming broadcast messages. The object contained in these message can be handled by the on_message handler function."""

    def __init__(
            self,
            port: int = 10002,
            on_message: Callable[[Packet, Address], None] = None,
            buffer_size: int = 65507,
            server_uuid: int = -1,
            stop_event = None
    ):
        super().__init__(daemon=True)
        if on_message is None:
            raise ValueError("on_message cant be null.")
        if not callable(on_message):
            raise TypeError("on_message must be callable.")

        self.server_uuid = server_uuid
        self.port = port
        self.on_message = on_message
        self.buffer_size = buffer_size
        self.blocked_ports = []
        self._stop_event = mp.Event() if stop_event is None else stop_event
        self.latest_uuids = deque(["" for _ in range(100)])

        self.port_queue = mp.Queue()

    def stop(self):
        self._stop_event.set()

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # alle interfaces
        sock.bind(("", self.port))
        # Timeout, damit recvfrom nicht unendlich blockiert
        sock.settimeout(5.0)

        self.port_queue.put(sock.getsockname()[1])
        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(self.buffer_size)
                    packet = Packet.decode(data)
                except socket.timeout:
                    # oft checken, ob wir stoppen sollen
                    continue
                except OSError:
                    # Socket wurde evtl. von außen geschlossen
                    break
                except json.JSONDecodeError:
                    # ungültiges JSON ignorieren
                    continue


                # if server_uuid is provided then ignore msgs by myself
                if self.server_uuid != -1 and self.server_uuid == packet.server_uuid:
                    continue

                # if packet uuid has been seen before lately, ignore
                if packet.packet_uuid in self.latest_uuids:
                    continue
                else:
                    self.latest_uuids.appendleft(packet.packet_uuid)
                    self.latest_uuids.pop()

                #Debug.log(f"Broadcast message received from {addr}, {packet.tag}")
                self.on_message(packet, addr)


        finally:
            sock.close()
            Debug.log("Broadcast listener stopped.")

    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()


class TCPConnection:
    """An abstract TCP connection which can be used for both sending and receiving packets."""

    T = TypeVar('T')

    # backlog wie viele verbindungsversuche gleichzeitig in der warteschlange sein dürfen
    def __init__(self, recv_queue: mp.Queue, stop_event = None):
        super().__init__()

        self._send_queue: mp.Queue[Packet] = mp.Queue()
        self._recv_queue: mp.Queue = recv_queue

        self._stop_event = mp.Event() if stop_event is None else stop_event

        self.socket: Optional[socket.socket] = None

    def send(self, packet: Packet):
        """Provides a packet to be sent as soon as possible."""
        self._send_queue.put(packet)

    def get_packet(self, timeout: Optional[float] = None):
        """Blocks and waits for an incoming packet. A timeout can be provided to cancel and return None."""
        try:
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop_event.set()

        #to stop the waiting for a package and it gets one immediatly
        try:
            self._send_queue.put_nowait(None)
        except Exception:
            pass

        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.socket.close()
            except OSError:
                pass

    def _send_queued_packets(self, conn: socket.socket):
        try:
            while True:
                packet: Packet = self._send_queue.get_nowait()
                conn.sendall(packet.encode())
        except queue.Empty:
            pass

    def sender(self, conn: socket.socket):
        while not self._stop_event.is_set():
            packet: Packet = self._send_queue.get()
            if packet is None:
                break
            try:
                conn.sendall(packet.encode())
            except OSError:
                break

    def receiver(self, conn: socket.socket):
        while not self._stop_event.is_set():
            try:
                packet = SocketUtils.recv_packet(conn)
                self._handle_packet(packet)

            except socket.timeout:
                continue

            except (ConnectionError, OSError) as e:
                try:
                    conn.close()
                except OSError:
                    pass
                break

    def _handle_packet(self, packet: Packet):
        raise NotImplementedError("Subclasses must implement _handle_packet method.")


class TCPClientConnection(TCPConnection, Thread):
    """A simple TCP client connection that can send and receive packets.
    """

    T = TypeVar('T')

    def __init__(self, address: tuple[str, int]):
        """
        Initializes a TCP client which connects to the specified address.
        """
        super().__init__(queue.Queue())

        self.address = address

    def run(self):
        # Establish connection
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Short timeout for initial connect
            self.socket.settimeout(5.0)
            self.socket.connect(self.address)
            # Use a small recv timeout so loop can check stop event
            self.socket.settimeout(0.1)
            print("Connected to server at ", self.address)
            print("TCP client address: ", self.socket.getsockname())

            # Start sender and receiver threads
            sender = Thread(target=self.sender, args=(self.socket,),daemon=True)
            receiver = Thread(target=self.receiver, args=(self.socket,),daemon=True)
            sender.start()
            receiver.start()

            sender.join()
            receiver.join()
            self.socket.close()
        except Exception:
            # Could not connect, exit thread
            print("Could not connect to server at ", self.address)
            self.socket.close()


class TCPServerConnection(TCPConnection, Thread):
    """A simple TCP server connection that accepts a single connection and can send and receive packets."""

    def __init__(self, conn_socket: socket.socket, recv_queue: mp.Queue, backlog: int = 1):
        """
        Initializes a TCP server which listens on the specified address.
        """
        super().__init__(recv_queue)
        self.backlog = backlog
        self.socket = conn_socket

        # Queue to communicate the actual port back to the parent process
        self.port_queue = mp.Queue()

    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()

    def run(self):

        self.port_queue.put(self.socket.getsockname()[1])

        # Start sender and receiver threads
        sender = Thread(target=self.sender, args=(self.socket,),daemon=True)
        receiver = Thread(target=self.receiver, args=(self.socket,),daemon=True)

        sender.start()
        receiver.start()
        sender.join()
        receiver.join()

        self.socket.close()
