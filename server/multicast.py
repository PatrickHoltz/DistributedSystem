"""Provides everything needed for multicasting messages"""
import socket
import struct
import threading
import time

from dataclasses import dataclass
from uuid import UUID
from multiprocessing import Process
from multiprocessing.connection import Listener,Client # used for inter process communication

from shared.sockets import Packet

@dataclass
class MulticastPacket:
    """Contains all the data needed for handling multicast packages"""
    packet: Packet
    sequence_number: int
    received_tracker: list[int]

class MulticastReceiver(Process):
    """Creates a new process for handling incoming multicast packages"""
    def __init__(self, group: str, port: int, ipc_port: int) -> None:
        super().__init__(daemon=True)

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', self.port))

        self.ipc = Client(('localhost', ipc_port))

    def run(self) -> None:
        register_msg = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, register_msg)

        while True:
            data = self.socket.recv(10240)
            self.ipc.send(data)

class MulticastSender(Process):
    """Creates a new process for sending multicast packages"""

    # for all packets sent, after two hops on the network the packet will not 
    # be re-sent/broadcast (see https://www.tldp.org/HOWTO/Multicast-HOWTO-6.html)
    MULTICAST_TTL = 1

    def __init__(self, group: str, port: int, ipc_port: int) -> None:
        super().__init__(daemon=True)

        self.group = group
        self.port = port

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.MULTICAST_TTL)

        self.ipc = Listener(('localhost', ipc_port))

    def run(self) -> None:
        ipc_connection = self.ipc.accept()

        while True:
            msg = ipc_connection.recv()
            self.socket.sendto(msg, (self.group, self.port))

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

        sender = MulticastSender(self.GROUP, self.PORT, self.IPC_SEND_PORT)
        sender.start()

        receiver = MulticastReceiver(self.GROUP, self.PORT, self.IPC_RECV_PORT)
        receiver.start()

        self.send_ipc = Client(('localhost', self.IPC_SEND_PORT))
        self.recv_ipc = Listener(('localhost', self.IPC_RECV_PORT))

        t = threading.Thread(target=self.testing, args=(), kwargs={"delay": 2})
        t.start()

        recv_connection = self.recv_ipc.accept()
        while True:
            msg = recv_connection.recv()
            print(msg)

    def testing(self) -> None:
        time.sleep(1)
        self.send_ipc.send("TEST")
        time.sleep(1)
        self.send_ipc.send("FOO")
        self.send_ipc.send("BAR")

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
