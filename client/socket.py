import socket
import json
from threading import Thread
from dataclasses import asdict


class TCPSocket(Thread):
    '''
    Send data to a server via TCP.
    '''
    def __init__(self, send_data):
        super().__init__()
        self.send_data = send_data

    def run(self):
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        server_address = '127.0.0.1'
        server_port = 10001

        tcp_socket.connect((server_address, server_port))


        send_bytes = json.dumps(asdict(self.send_data)).encode() + b"\n"
        tcp_socket.sendall(send_bytes)


        tcp_socket.close()