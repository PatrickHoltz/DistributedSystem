"""Provides everything needed for multicasting messages"""
import socket
import struct

from dataclasses import dataclass
from uuid import UUID
from threading import Thread, Lock

from shared.sockets import Packet

@dataclass
class MulticastPacket:
    """Contains all the data needed for handling multicast packages"""
    sender_uuid: UUID
    sequence_number: int

    # contains the actually send msg
    content: str

    @staticmethod
    def get_format_str(content_size: int) -> str:
        return "!I16sI" + str(content_size) + "s"

    @staticmethod
    def get_packet_size(content_size: int) -> int:
        # returns the remaining size of the package after reading in the first 4 bytes defining the content size
        return 16 + 4 + content_size

    def pack(self) -> bytes:
        uuid = self.sender_uuid.bytes
        content = bytearray(self.content.encode('utf-8'))
        content_size = len(content)

        format = MulticastPacket.get_format_str(content_size)

        return struct.pack(format, content_size, uuid, self.sequence_number, content)

    @staticmethod
    def unpack(data: bytes) -> MulticastPacket:
        content_size = int.from_bytes(data[0:4], "big", False)
        format = MulticastPacket.get_format_str(content_size)

        (content_size, uuid_bytes, sequence_number, content_bytes) = struct.unpack(format, data)

        uuid = UUID(bytes=uuid_bytes)
        content = content_bytes.decode("utf-8")
        return MulticastPacket(uuid, sequence_number, content)

class MulticastReceiver(Thread):
    """Creates a new thread for handling incoming multicast packages"""

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int, ipc_port: int) -> None:
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
            msg = self._receive_package()

            self.lock.acquire()
            self.msg_queue.append(msg)
            self.lock.release()

    def has_msgs(self) -> bool:
        return len(self.msg_queue) > 0

    def get(self) -> MulticastPacket|None:
        msg = None

        self.lock.acquire()
        if len(self.msg_queue) > 0:
            msg = self.msg_queue.pop()
        self.lock.release()

        return msg

    def _receive_package(self) -> MulticastPacket:
        length_bytes = self._receive_exact(4)
        content_size = int.from_bytes(length_bytes, "big", False)
        package_size = MulticastPacket.get_packet_size(content_size)
        package_bytes = self._receive_exact(package_size)
        data = length_bytes + package_bytes

        return MulticastPacket.unpack(data)

    def _receive_exact(self, size) -> bytes:
        data: bytes = []
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            data += chunk
        return data

class MulticastSender(Thread):
    """Creates a new thread for sending multicast packages"""

    # how many network hops the msg will take (see https://www.tldp.org/HOWTO/Multicast-HOWTO-6.html)
    MULTICAST_TTL = 1

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int, ipc_port: int) -> None:
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
                self.lock.acquire()
                msg = self.msg_queue.pop()
                self.lock.release()

                bytes = msg.pack()
                self.socket.sendto(bytes, (self.group, self.port))

    def send(self, msg: MulticastPacket):
        self.lock.acquire()
        self.msg_queue.append(msg)
        self.lock.release()

class Multicast:
    """Class for sending and receiving reliably ordered (FIFO) multicasts"""

    GROUP = '224.0.0.1'
    PORT = 5007

    IPC_SEND_PORT = 6000
    IPC_RECV_PORT = 6001

    uuid: UUID

    _sender: MulticastSender
    _receiver: MulticastReceiver
    _receive_handler: Thread

    _sequence_number: int
    _received_tracker: dict[UUID, int]
    
    _hold_back_queue: list[MulticastPacket]

    def __init__(self, uuid) -> None:
        self.uuid = uuid;

        self._sequence_number = 0
        self._received_tracker = {}
        self._hold_back_queue = []

        self._sender = MulticastSender(self.GROUP, self.PORT, self.IPC_SEND_PORT)
        self._receiver = MulticastReceiver(self.GROUP, self.PORT, self.IPC_RECV_PORT)

        self._sender.start()
        self._receiver.start()

        self._receive_handler = Thread(target=self._receive_handler, args=())
        self._receive_handler.start()

    def cast_msg(self, msg: str):
        self._reliable_multicast(msg)

    def _receive_handler(self):
        while True:
            if self._receiver.has_msgs():
                msg = self._receiver.get()
                print("RECV: " + str(msg.sequence_number))
                self._on_receive(msg)

    # basic multicast
    def _on_receive(self, msg: MulticastPacket):
        tracker = self._received_tracker.get(msg.sender_uuid)
        if msg.sequence_number == tracker or tracker == None:
            self._received_tracker[msg.sender_uuid] = msg.sequence_number + 1

            self._basic_deliver(msg)

            self._clear_hold_back_queue(msg.sender_uuid)

        elif msg.sequence_number > tracker:
            # TODO: request missing message
            self._hold_back_queue.append(msg)

        # else ignore duplicate msg

    #def _basic_multicast(self, content: str):
    #    msg = MulticastPacket(self.uuid, self._sequence_number, content)
    #
    #    self._sequence_number += 1
    #    self._sender.send(msg)

    def _basic_deliver(self, msg: MulticastPacket):
        if msg in self._received:
            return
        
        self._received.append(msg)
        
        if msg.sender_uuid != self.uuid:
            self._reliable_multicast(msg) # TODO: check if this needs to be basic_multicast

        self._reliable_deliver(msg)

    def _reliable_multicast(self, content: str): # TODO: make this handle concrete packets
        msg = MulticastPacket(self.uuid, self._sequence_number, content) # TODO: add self._received_tracker

        self._sequence_number += 1
        self._sender.send(msg)

    def _reliable_deliver(self, msg: MulticastPacket):
        #if msg.sender_uuid != self.uuid:
            print("Deliver: "+ str(msg.sequence_number) + " > " + msg.content)

    def _clear_hold_back_queue(self, sender_uuid: UUID):
        tracker = self._received_tracker.get(sender_uuid)
        next = None
        for msg in self._hold_back_queue:
            if msg.sender_uuid != sender_uuid:
                continue

            if msg.sequence_number == tracker:
                next = msg
                break

        if next != None:
            self._hold_back_queue.remove(next)
            self._on_receive(next)
