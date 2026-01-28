"""This module contains classes for sending packets in different forms."""

import socket
from collections import deque
from threading import Thread, Event
import multiprocessing as mp
from concurrent.futures import Future
import json
from dataclasses import asdict, is_dataclass
import struct
import queue
from typing import Optional, Tuple, Callable, TypeVar, Type
from enum import StrEnum
from uuid import uuid4, UUID


class PacketTag(StrEnum):
    NONE = "none"
    LOGIN = "login"
    LOGOUT = "logout"
    ATTACK = "attack"
    PLAYER_GAME_STATE = "player_game_state"
    STRING_MESSAGE = "string_message"
    NEW_BOSS = "new_boss"
    BOSS_DEAD = "boss_dead"
    CLIENT_PING = "client_ping"
    CLIENT_PONG = "client_pong"
    SERVER_HELLO = "server_hello"
    LOGIN_REPLY = "login_reply"

    #Bully
    BULLY_ELECTION = "election"
    BULLY_OK = "ok"
    BULLY_COORDINATOR = "coordinator"
    BULLY_LEADER_HEARTBEAT = "leader_heartbeat"

class Packet:
    """Basic packet for client-server communication.
    """

    def __init__(self, content: object | dict, tag: PacketTag = PacketTag.NONE, packet_uuid: str = str(uuid4()), server_uuid: int = -1, length: int = 0):
        """Content must be a dataclass"""
        self.content = content
        self.tag = tag
        self.packet_uuid = packet_uuid
        self.server_uuid = server_uuid
        
        self._length = length

    def _to_dictionary(self) -> dict[str, object]:
        if not is_dataclass(self.content):
            raise TypeError("Could not encode packet. Packet content must be a dataclass.")
        dictionary = dict()
        dictionary['tag'] = self.tag.value
        dictionary['packet_uuid'] = self.packet_uuid
        dictionary['server_uuid'] = self.server_uuid
        dictionary['content'] = asdict(self.content)
        return dictionary

    def encode(self) -> bytes:
        """Encodes a packet to a byte array ready to send. The returned bytes start with a 4 byte data length.
        """
        json_string = json.dumps(self._to_dictionary())
        json_bytes = json_string.encode()

        # prepend the data length as a 4-byte integer
        data_length = struct.pack('!I', len(json_bytes))
        return data_length + json_bytes

    @classmethod
    def decode(cls, data: bytes) -> 'Packet':
        """Decodes a byte array into a packet ready to use. The content is kept as a dictionary.
        """
        data_length = int.from_bytes(data[0:4])
        dictionary = json.loads(data[4:].decode())
        return cls(dictionary['content'], PacketTag(dictionary["tag"]), str(dictionary["packet_uuid"]), int(dictionary["server_uuid"]) , data_length)


class UDPSocket(Thread):
    """
    Send data to a server via UDP.
    """

    def __init__(self, packet: Packet, server_address: str, server_port: int):
        super().__init__()
        self.packet = packet
        self.server_address = server_address
        self.server_port = server_port

    def run(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        print("Connecting to: ", (self.server_address, self.server_port))
        udp_socket.connect((self.server_address, self.server_port))

        udp_socket.sendall(self.packet.encode())

        udp_socket.close()


class BroadcastSocket(Thread):
    """Broadcasts a single packet to the specified broadcast address using a new socket. A response can be obtained."""

    BROADCAST_IP = "255.255.255.255"

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

    @classmethod
    def calculate_broadcast(cls, ip, mask):
        ip_int = struct.unpack("!I", socket.inet_aton(ip))[0]
        mask_int = struct.unpack("!I", socket.inet_aton(mask))[0]
        broadcast_int = ip_int | (~mask_int & 0xFFFFFFFF)
        return socket.inet_ntoa(struct.pack("!I", broadcast_int))


class BroadcastListener(Thread):
    """A thread that listens to incoming broadcast messages. The object contained in these message can be handled by the on_message handler function."""

    def __init__(
            self,
            port: int = 10002,
            on_message: Callable[[Packet, tuple[str, int]], Packet] = None,
            buffer_size: int = 65507,
            server_uuid: int = -1
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
        self._stop_event = Event()
        self.latest_uuids = deque(["" for _ in range(100)])

    def stop(self):
        self._stop_event.set()

    def run(self):
        local_ip = self.get_local_ip()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # alle interfaces
        sock.bind(("", self.port))
        # Timeout, damit recvfrom nicht unendlich blockiert
        sock.settimeout(5.0)
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
                    # print("Broadcast message received from ", addr)
                    content = Packet.decode(data)
                except json.JSONDecodeError:
                    # ungültiges JSON ignorieren
                    continue


                if self.server_uuid != -1:
                    # if uuid is provided then ignore msgs from myself
                    if self.server_uuid == content.server_uuid:
                        continue

                    # if packet uuid has been seen before lately, ignore
                    if content.packet_uuid in self.latest_uuids:
                        continue
                    else:
                        self.latest_uuids.appendleft(content.packet_uuid)
                        self.latest_uuids.pop()

                response_packet: Packet = self.on_message(content, addr)
                if response_packet:
                    sock.sendto(response_packet.encode(), addr)


        finally:
            sock.close()

    @staticmethod
    def get_local_ip():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connect to a dummy address (8.8.8.8) to trigger the OS
            # to pick the correct outgoing interface. No data is actually sent.
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]


class _TCPConnection:
    """An abstract TCP connection which can be used for both sending and receiving packets."""

    T = TypeVar('T')

    # backlog wie viele verbindungsversuche gleichzeitig in der warteschlange sein dürfen
    def __init__(self, address: Tuple[str, int], recv_queue: mp.Queue):
        super().__init__()
        self.address = address

        self._send_queue: mp.Queue[Packet] = mp.Queue()
        self._recv_queue: mp.Queue = recv_queue

        self._stop_event = mp.Event()



        self.socket: Optional[socket.socket] = None

    @classmethod
    def _recv_exact(cls, n_bytes: int, conn: socket.socket, timeout_s: float = None):
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

    @classmethod
    def get_typed_packet(cls, packet: Packet, content_type: Type[T]) -> Packet | None:
        """
        Extracts the content of a packet and converts it to the specified type.
        Returns None if the conversion fails.
        """
        try:
            content = content_type(**packet.content)
            return Packet(content=content, tag=packet.tag)
        except TypeError:
            return None

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


    def _recv_packet(self, sock: socket.socket) -> Packet:
        """Blocks until a full packet is received from the socket.
        """
        length_bytes = self._recv_exact(4, sock)
        data_length = struct.unpack('!I', length_bytes)[0]
        json_bytes = self._recv_exact(data_length, sock)
        packet = Packet.decode(length_bytes + json_bytes)
        return packet

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
                packet = self._recv_packet(conn)
                self._handle_packet(packet)
            except (ConnectionError, OSError):
                try:
                    conn.close()
                except OSError:
                    pass
                break

    def _handle_packet(self, packet: Packet):
        raise NotImplementedError("Subclasses must implement _handle_packet method.")


class TCPClientConnection(_TCPConnection, Thread):
    """A simple TCP client connection that can send and receive packets.
    """

    T = TypeVar('T')

    def __init__(self, address: Tuple[str, int]):
        """
        Initializes a TCP client which connects to the specified address.
        """
        super().__init__(address, queue.Queue())

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


class TCPServerConnection(_TCPConnection, mp.Process):
    """A simple TCP server connection that accepts a single connection and can send and receive packets."""

    def __init__(self, recv_queue: mp.Queue, address: Tuple[str, int] = ("", 0), backlog: int = 1):
        """
        Initializes a TCP server which listens on the specified address.
        """
        super().__init__(address, recv_queue)
        self.address = address
        self.backlog = backlog

        # Queue to communicate the actual port back to the parent process
        self.port_queue = mp.Queue()

    def get_port(self) -> int:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.port_queue.get()

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # damit man nach crash direkt wieder benutzen kann
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.address)

        self.socket.listen(self.backlog)

        self.port_queue.put(self.socket.getsockname()[1])

        # accept a single connection
        self.socket.settimeout(None)
        conn, client_address = self.socket.accept()
        print("TCP connection established with ", client_address)

        # Start sender and receiver threads
        sender = Thread(target=self.sender, args=(conn,),daemon=True)
        receiver = Thread(target=self.receiver, args=(conn,),daemon=True)

        sender.start()
        receiver.start()
        sender.join()
        sender.join()

        if conn is not None:
            conn.close()
        self.socket.close()
