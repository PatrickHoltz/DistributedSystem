import socket
import json
from threading import Thread
from dataclasses import asdict
from shared.packet import Packet


class TCPSocket(Thread):
    '''
    Send data to a server via TCP.
    '''
    def __init__(self, packet: Packet,server_address: str , server_port: int):
        super().__init__()
        self.packet = packet
        self.server_address = server_address
        self.server_port = server_port

    def run(self):
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        print("Connecting to: ", (self.server_address, self.server_port))
        tcp_socket.connect((self.server_address, self.server_port))

        tcp_socket.sendall(self.packet.encode())

        tcp_socket.close()

class BroadcastSocket(Thread):
    def __init__(
        self,
        send_data,
        broadcast_port: int = 10002,
        broadcast_address: str = "<broadcast>"
    ):
        super().__init__()
        self.send_data = send_data
        self.broadcast_port = broadcast_port
        self.broadcast_address = broadcast_address

    def run(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # für uns: wiederberwendung der Adresse
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        payload = asdict(self.send_data)
        send_bytes = json.dumps(payload).encode("utf-8")

        udp_socket.sendto(send_bytes, (self.broadcast_port, self.broadcast_address))
        udp_socket.close()

