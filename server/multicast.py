"""Provides everything needed for multicasting messages"""
import socket
import struct

from dataclasses import dataclass
from uuid import UUID
from multiprocessing import Process

from shared.sockets import Packet

@dataclass
class MulticastPacket:
    """Contains all the data needed for handling multicast packages"""
    packet: Packet
    sequence_number: int
    received_tracker: list[int]

class MulticastReceiver(Process):
    """Creates a new process for handling incoming multicast packages"""
    def __init__(self, group: str, port: int) -> None:
        super().__init__(daemon=True)

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', self.port))

    def run(self) -> None:
        register_msg = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, register_msg)

        while True:
            print(self.socket.recv(10240))

class MulticastSender(Process):
    """Creates a new process for sending multicast packages"""

    # for all packets sent, after two hops on the network the packet will not 
    # be re-sent/broadcast (see https://www.tldp.org/HOWTO/Multicast-HOWTO-6.html)
    MULTICAST_TTL = 1

    def __init__(self, group: str, port: int) -> None:
        super().__init__(daemon=True)

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.MULTICAST_TTL)

    def run(self) -> None:
        self.socket.sendto(b"robot", (self.group, self.port))

class Multicast:
    """"""

    GROUP = '224.0.0.1'
    PORT = 5007

    msgs: list[MulticastPacket]
    sequence_number: int
    received_tracker: dict[UUID, int]

    def __init__(self) -> None:
        self.msgs = []

        receiver = MulticastReceiver(self.GROUP, self.PORT)
        receiver.start()

        sender = MulticastSender(self.GROUP, self.PORT)
        sender.start()
        
        while True:
            pass

    def cast_msg(self, msg):
        pass

    def _on_basic_deliver(self, msg):
        # ignore duplicates
        if msg in self.msgs:
            return

        self.msgs.append(msg)

    def _on_receive(self, msg):
        pass
        #match(msg):
        #    case (msg.sequence_number == )

    def _create_receive_socket(self):
        pass
