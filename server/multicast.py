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
    def get_format_str() -> str:
        return '!16si1024s' # TODO: make this not fixed

    def pack(self) -> bytes:
        uuid = self.sender_uuid.bytes
        chars = bytearray(self.content.encode('utf-8'))
        return struct.pack(MulticastPacket.get_format_str(), (uuid, self.sequence_number, chars))

    @staticmethod
    def unpack(data: bytes) -> MulticastPacket:
        tuple = struct.unpack(MulticastPacket.get_format_str(), data)
        uuid = UUID(tuple[0])
        sequence_number = tuple[1]
        content = tuple[2].decode("utf-8")
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
            data = self.socket.recv(1024) # TODO: make this not fixed
            msg = MulticastPacket.unpack(data)
            
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

    _msgs: list[MulticastPacket]
    _sequence_number: int
    _received_tracker: dict[UUID, int]
    
    _hold_back_queue: list[MulticastPacket]

    def __init__(self, uuid) -> None:
        self._msgs = []
        
        self.uuid = uuid;

        self._sender = MulticastSender(self.GROUP, self.PORT, self.IPC_SEND_PORT)
        self._sender.start()

        self._receiver = MulticastReceiver(self.GROUP, self.PORT, self.IPC_RECV_PORT)
        self._receiver.start()

        self._receive_handler = Thread(target=self._receive_handler, args=())
        self._receive_handler.start()

    def cast_msg(self, msg: str):
        self._basic_multicast(MulticastPacket(msg))

    def _receive_handler(self):
        while True:
            if self._receiver.has_msgs():
                msg = self._receiver.get()
                self._on_receive(msg)

    # basic multicast
    def _on_receive(self, msg: MulticastPacket):
        tracker = self._received_tracker.get(msg.sender_uuid)
        if msg.sequence_number == tracker or tracker == None:
            self._received_tracker[msg.sender_uuid] = tracker + 1
            self._basic_deliver(msg)
            
            # TODO: clear hold back queue

        elif msg.sequence_number > tracker:
            # TODO: request missing message
            self._hold_back_queue.append(msg)

        # else ignore duplicate msg

    def _basic_multicast(self, msg: MulticastPacket):
        msg.sender_uuid = self.uuid
        msg.sequence_number = self._sequence_number
        self._sender.send(msg)
    
    def _basic_deliver(self, msg: MulticastPacket):
        print(msg.content)

    def _on_basic_deliver(self, msg):
        # ignore duplicates
        if msg in self._msgs:
            return

        self._msgs.append(msg)
