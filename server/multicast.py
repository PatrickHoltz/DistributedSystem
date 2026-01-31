"""Provides everything needed for multicasting messages"""
import socket
import struct

from dataclasses import dataclass
from logging import warning
from threading import Thread, Lock
from uuid import UUID

@dataclass
class MulticastPacket:
    """Contains all the data needed for handling multicast packages"""
    sender_uuid: UUID
    sequence_number: int

    # contains the actually send msg
    content: str

    FIX_SIZE: int = 4096

    @staticmethod
    def get_format_str() -> str:
        content_size = MulticastPacket.FIX_SIZE - 16 - 4
        return '!16si' + str(content_size) + 's'

    def pack(self) -> bytes:
        uuid = self.sender_uuid.bytes
        chars = bytearray(self.content.encode('utf-8'))
        data = struct.pack(MulticastPacket.get_format_str(), uuid, self.sequence_number, chars)

        if len(data) > MulticastPacket.FIX_SIZE:
            warning("MulticastPacket exceeds the set fixed size of " + str(MulticastPacket.FIX_SIZE) + " (actual size: " + str(len(data)) + ")")

        return data

    @staticmethod
    def unpack(data: bytes) -> 'MulticastPacket':
        uuid_bytes, sequence_number, content_bytes= struct.unpack(MulticastPacket.get_format_str(), data)
        uuid = UUID(bytes=uuid_bytes)
        content = content_bytes.decode("utf-8")

        return MulticastPacket(uuid, sequence_number, content)

class MulticastReceiver(Thread):
    """Creates a new thread for handling incoming multicast packages"""

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int) -> None:
        super().__init__(daemon=True)

        self.msg_queue = []
        self.lock = Lock()

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', self.port))

    def run(self) -> None:
        config = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, config)

        while True:
            data = self.socket.recv(MulticastPacket.FIX_SIZE)
            msg = MulticastPacket.unpack(data)

            with self.lock:
                self.msg_queue.append(msg)

    def has_msgs(self) -> bool:
        return len(self.msg_queue) > 0

    def get(self) -> MulticastPacket|None:
        msg = None

        with self.lock:
            if len(self.msg_queue) > 0:
                msg = self.msg_queue.pop()

        return msg

class MulticastSender(Thread):
    """Creates a new thread for sending multicast packages"""

    # how many network hops the msg will take (see https://www.tldp.org/HOWTO/Multicast-HOWTO-6.html)
    MULTICAST_TTL = 1

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int) -> None:
        super().__init__(daemon=True)

        self.msg_queue = []
        self.lock = Lock()

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.MULTICAST_TTL)

    def run(self) -> None:
        while True:
            if len(self.msg_queue) > 0:
                with self.lock:
                    msg = self.msg_queue.pop()

                msg_bytes = msg.pack()
                self.socket.sendto(msg_bytes, (self.group, self.port))

    def send(self, msg: MulticastPacket):
        with self.lock:
            self.msg_queue.append(msg)

class Multicast:
    """Class for sending and receiving reliably ordered (FIFO) multicasts"""

    GROUP = '224.0.0.1'
    PORT = 5007

    uuid: UUID

    _sender: MulticastSender
    _receiver: MulticastReceiver
    _receive_handler: Thread

    _sequence_number: int
    _received_tracker: dict[UUID, int]

    _hold_back_queue: list[MulticastPacket]

    def __init__(self, uuid) -> None:
        self.uuid = uuid

        self._sequence_number = 0
        self._received_tracker = {}
        self._hold_back_queue = []

        self._sender = MulticastSender(self.GROUP, self.PORT)
        self._receiver = MulticastReceiver(self.GROUP, self.PORT)

        self._sender.start()
        self._receiver.start()

        self._receive_handler = Thread(target=self._receive_loop, args=())
        self._receive_handler.start()

    def cast_msg(self, msg: str):
        self._basic_multicast(msg)

    def _receive_loop(self):
        while True:
            if self._receiver.has_msgs():
                msg = self._receiver.get()
                if msg is None:
                    continue

                print("RECV: " + str(msg.sequence_number))
                self._on_receive(msg)

    # basic multicast
    def _on_receive(self, msg: MulticastPacket):
        tracker = self._received_tracker.get(msg.sender_uuid)
        if msg.sequence_number == tracker or tracker is None:
            self._received_tracker[msg.sender_uuid] = msg.sequence_number + 1

            self._basic_deliver(msg)

            self._clear_hold_back_queue(msg.sender_uuid)

        elif msg.sequence_number > tracker:
            # TODO: request missing message
            self._hold_back_queue.append(msg)

        # else ignore duplicate msg

    def _basic_multicast(self, content: str):
        msg = MulticastPacket(self.uuid, self._sequence_number, content)

        self._sequence_number += 1
        self._sender.send(msg)

    def _basic_deliver(self, msg: MulticastPacket):
        #if msg.sender_uuid != self.uuid:
            print("Deliver: "+ str(msg.sequence_number) + " > " + msg.content)

    def _clear_hold_back_queue(self, sender_uuid: UUID):
        tracker = self._received_tracker.get(sender_uuid)
        next_msg = None
        for msg in self._hold_back_queue:
            if msg.sender_uuid != sender_uuid:
                continue

            if msg.sequence_number == tracker:
                next_msg = msg
                break

        if next_msg is not None:
            self._hold_back_queue.remove(next_msg)
            self._on_receive(next_msg)
