import socket
import json
from threading import Thread, Event
from dataclasses import asdict
import struct
import ipaddress

class Packet():
    """Basic packet for client-server communication.
    """

    def __init__(self, content: object, length: int = 0):
        """Content must be a dataclass"""
        self._content = content
        self._length = length

    def get_length(self) -> int:
        return len(json.dumps(asdict(self._content)))
    

    def __to_dictionary(self) -> dict[str, object]:
        dictionary = dict()
        dictionary['length'] = self.get_length()
        dictionary['content'] = asdict(self._content)
        return dictionary

    def encode(self) -> bytes:
        """Encodes a packet to a byte array ready to send.
        """
        json_string = json.dumps(self.__to_dictionary())
        return json_string.encode()

    @classmethod
    def decode(cls, data: bytes) -> 'Packet':
        """Decodes a byte array into a packet ready to use.
        """
        dictionary = json.loads(data.decode())
        return cls(dictionary['content'], dictionary["length"])

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
    '''Broadcasts a single packet to the specified broadcast address. For that, a new socket is created.'''
    def __init__(
        self,
        packet: Packet,
        broadcast_port: int = 10002
    ):
        super().__init__()
        self.packet = packet
        self.broadcast_port = broadcast_port
        self.broadcast_address = "" # here your local broadcast address should be entered

    def run(self):
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # für uns: wiederberwendung der Adresse
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bytes = self.packet.encode()
        udp_socket.sendto(bytes, (self.broadcast_address, self.broadcast_port))
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
    def __init__(self, port: int = 10002, on_message: callable=None, buffer_size: int = 4096):
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
                    print("Listening for broadcast messages...")
                    data, addr = sock.recvfrom(self.buffer_size)
                except socket.timeout:
                    # oft checken, ob wir stoppen sollen
                    continue
                except OSError:
                    # Socket wurde evtl. von außen geschlossen
                    break
                try:
                    content = Packet.decode(data)._content
                except json.JSONDecodeError:
                    # ungültiges JSON ignorieren
                    continue

                self.on_message(content, addr)

        finally:
            sock.close()