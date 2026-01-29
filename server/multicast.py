"""Provides everything needed for multicasting messages"""
import socket
import struct
import threading
import time

from dataclasses import dataclass
from uuid import UUID
from threading import Thread, Lock

from shared.sockets import Packet

@dataclass
class MulticastPacket:
    """Contains all the data needed for handling multicast packages"""
    #packet: Packet
    #sequence_number: int
    #received_tracker: list[int]
    
    test: str
    
    @staticmethod
    def get_format_str() -> str:
        return '!1024s' # TODO: make this not fixed
    
    def pack(self) -> bytes:
        chars = bytearray(self.test.encode('utf-8'))
        return struct.pack(MulticastPacket.get_format_str(), (chars))

    @staticmethod
    def unpack(data: bytes) -> MulticastPacket:
        tuple = struct.unpack(MulticastPacket.get_format_str(), data)
        test = tuple[0].decode("utf-8")
        return MulticastPacket(test)
        
        

class MulticastReceiver(Thread):
    """Creates a new process for handling incoming multicast packages"""
    
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
    """Creates a new process for sending multicast packages"""

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
    """"""

    GROUP = '224.0.0.1'
    PORT = 5007

    IPC_SEND_PORT = 6000
    IPC_RECV_PORT = 6001

    msgs: list[MulticastPacket]
    sequence_number: int
    received_tracker: dict[UUID, int]

    def __init__(self) -> None:
        self.msgs = []

        self.sender = MulticastSender(self.GROUP, self.PORT, self.IPC_SEND_PORT)
        self.sender.start()

        self.receiver = MulticastReceiver(self.GROUP, self.PORT, self.IPC_RECV_PORT)
        self.receiver.start()

        t = threading.Thread(target=self.testing, args=())
        t.start()

        while True:
            if self.receiver.has_msgs():
                msg = self.receiver.get()
                print("> NEW MSG: " + msg.test)

    def testing(self) -> None:
        time.sleep(3)
        self.sender.send(MulticastPacket("TEST"))
        time.sleep(3)
        self.sender.send(MulticastPacket("FOO"))
        self.sender.send(MulticastPacket("BAR"))
        time.sleep(3)

    def cast_msg(self, msg):
        pass

    def _on_basic_deliver(self, msg):
        # ignore duplicates
        if msg in self.msgs:
            return

        self.msgs.append(msg)

    def _on_receive(self, msg):
        print(msg)
        pass
        #match(msg):
        #    case (msg.sequence_number == )

    def _create_receive_socket(self):
        pass
