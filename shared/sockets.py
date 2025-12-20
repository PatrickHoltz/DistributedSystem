'''This module contains classes for sending packets in different forms.'''

import socket
from threading import Thread, Event
import multiprocessing as mp
from concurrent.futures import Future
import json
from dataclasses import asdict, is_dataclass
import struct
import ipaddress
import queue
from typing import Optional, Tuple, Callable
from enum import StrEnum


class PacketTag(StrEnum):
    NONE = "none"
    LOGIN = "login"
    LOGOUT = "logout"
    ATTACK = "attack"
    PLAYERGAMESTATE = "playergamestate"
    STRINGMESSAGE = "stringmessage"
    NEW_BOSS = "new_boss"
    BOSS_DEAD = "boss_dead"
    Client_Ping = "client_ping"
    Client_Pong = "client_pong"


class Packet():
    """Basic packet for client-server communication.
    """

    def __init__(self, content: object | dict, tag: PacketTag = PacketTag.NONE, length: int = 0):
        """Content must be a dataclass"""
        self._content = content
        self._length = length
        self._tag = tag
    

    def __to_dictionary(self) -> dict[str, object]:

        if not is_dataclass(self._content):
            raise "Could not encode package. Package content must be a dataclass."
        dictionary = dict()
        dictionary['tag'] = self._tag.value
        dictionary['content'] = asdict(self._content)
        return dictionary

    def encode(self) -> bytes:
        """Encodes a packet to a byte array ready to send. The returned bytes start with a 4 byte data length.
        """
        json_string = json.dumps(self.__to_dictionary())
        json_bytes = json_string.encode()

        # prepend the data length as a 4-byte integer
        data_length = struct.pack('!I', len(json_bytes))
        return data_length + json_bytes

    @classmethod
    def decode(cls, data: bytes) -> 'Packet':
        """Decodes a byte array into a packet ready to use. The content is kept as a dictionary.
        """
        data_length = data[0:4]
        dictionary = json.loads(data[4:].decode())
        return cls(dictionary['content'], PacketTag(dictionary["tag"]), data_length)

class UDPSocket(Thread):
    '''
    Send data to a server via UDP.
    '''
    def __init__(self, packet: Packet,server_address: str , server_port: int):
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
    '''Broadcasts a single packet to the specified broadcast address using a new socket. A response can be obtained.'''
    def __init__(
        self,
        packet: Packet,
        response_handler: Callable[[Packet, tuple[str, int]], None] = None,
        response_timeout_handler: Callable = None,
        broadcast_port: int = 10002,
    ):
        super().__init__()
        self.send_packet = packet
        self.broadcast_port = broadcast_port
        self.broadcast_address = "" # here your local broadcast address should be entered
        self.future: Future[Packet] = Future()
        self.server_address: tuple[str, int] = None
        self.response_handler = response_handler
        self.response_timeout_handler = response_timeout_handler

    def run(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.settimeout(5.0)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # für uns: wiederberwendung der Adresse
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        bytes = self.send_packet.encode()
        udp_socket.sendto(bytes, (self.broadcast_address, self.broadcast_port))

        try:
            data, self.server_address = udp_socket.recvfrom(4096)
            received_packet: Packet = Packet.decode(data)
            if self.response_handler:
                self.response_handler(received_packet, self.server_address)
        except socket.timeout:
            self.response_timeout_handler()
        udp_socket.close()

    @classmethod
    def calculate_broadcast(cls, ip, mask):
        ipaddress.ip_network
        ip_int = struct.unpack("!I", socket.inet_aton(ip))[0]
        mask_int = struct.unpack("!I", socket.inet_aton(mask))[0]
        broadcast_int = ip_int | (~mask_int & 0xFFFFFFFF)
        return socket.inet_ntoa(struct.pack("!I", broadcast_int))


class BroadcastListener(Thread):
    '''A thread that listens to incoming broadcast messages. The object contained in these message can be handled by the on_message handler function.'''

    def __init__(self, port: int = 10002, on_message: Callable[[Packet, tuple[str, int]], Packet] = None, buffer_size: int = 65507):
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
                    print("Broadcast message received")
                    content = Packet.decode(data)
                except json.JSONDecodeError:
                    # ungültiges JSON ignorieren
                    continue

                response_packet: Packet = self.on_message(content, addr)
                response_data = response_packet.encode()
                if response_packet:
                    sock.sendto(response_data, addr)


        finally:
            sock.close()


class TCPConnection(mp.Process):
    '''A ongoing TCP connection which can be used for both sending and receiving packets.'''

    # backlog wie viele verbindungsversuche gleichzeitig in der warteschlange sein dürfen
    def __init__(self, address: Tuple[str, int], backlog: int = 1, buffer_size: int = 4096):
        super().__init__(daemon=True)
        self.address = address
        self.backlog = backlog
        self.buffer_size = buffer_size

        self._send_queue: mp.Queue[Packet] = mp.Queue()
        self._recv_queue: mp.Queue[Packet] = mp.Queue()

        self._stop_event = mp.Event()

    def send(self, packet: Packet):
        '''Provides a packet to be sent as soon as possible.'''
        self._send_queue.put(packet)

    def get_packet(self, timeout: Optional[float] = None):
        '''Blocks and waits for an incoming packet. A timeout can be provided to cancel after some time.'''
        try:
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop_event.set()

    def _recv_exact(self, n_bytes: int, conn: socket):
        data = b""
        while len(data) < n_bytes:
            chunk = conn.recv(n_bytes - len(data))
            if not chunk:
                conn.close()
                raise ConnectionError("connection closed")
            data += chunk
        return data


    def run(self):

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # damit man nach crash direkt wieder benutzenm kann
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(self.address)
        server_sock.listen(self.backlog)
        server_sock.settimeout(1.0)

        conn = None

        try:
            while not self._stop_event.is_set():
                # Wait for a connection
                if conn is None:
                    try:
                        conn, client_address = server_sock.accept()
                        conn.settimeout(0.1)
                    except socket.timeout:
                        continue

                # Send outgoing packets
                try:
                    while True:
                        packet: Packet = self._send_queue.get_nowait()
                        data = packet.encode()
                        conn.sendall(data)
                except queue.Empty:
                    pass

                # Read a single incoming packet (if available)
                try:
                    length_bytes = self._recv_exact(4, conn)
                    data_length = struct.unpack('!I', length_bytes)[0]
                    json_bytes = self._recv_exact(data_length, conn)
                    packet = Packet.decode(length_bytes + json_bytes)
                    self._recv_queue.put(packet)
                except ConnectionError:
                    conn.close()
                    conn = None
                except socket.timeout:
                    #kein neues paket
                    pass

        finally:
            if conn is not None:
                conn.close()
            server_sock.close()
