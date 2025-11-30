"""Module containing all the commonly shared classes and functions between server and client.
"""

import json


SERVER_ADDR = '127.0.0.1'
SERVER_PORT = 10002

class Packet:
    """Basic packet for client-server communication 
        containing a reference for the type of message content.
    """


    def __init__(self, name: str):
        self.name = name

    def to_dictionary(self) -> dict[str, object]:
        """ Creates a dictionary containing all the data of this packet.
        """
        return {
            'name': self.name,
        }

    @classmethod
    def from_dictionary(cls, dictionary: dict) -> 'Packet':
        """ Creates a packet from a dictionary.
        """
        return cls(dictionary['name'])

class Login(Packet):
    """Packet send by the client to login at the server.
    """
    def __init__(self):
        super().__init__("Login")

    @classmethod
    def from_dictionary(cls, dictionary: dict) -> 'Login':
        return cls()

class Logout(Packet):
    """Packet send by the client to logout at the server.
    """
    def __init__(self, uuid):
        super().__init__("Logout")

        self.uuid = uuid

    def to_dictionary(self) -> dict[str, object]:
        dictionary = super().to_dictionary()

        dictionary['uuid'] = self.uuid
        return dictionary

    @classmethod
    def from_dictionary(cls, dictionary: dict) -> 'Logout':
        return cls(dictionary['uuid'])

class Message(Packet):
    """Packet containing a chat message.
    """

    def __init__(self, time: int, author: str, content: str, length: int = 0):
        super().__init__("Message")

        self._length = length
        self.time = time
        self.author = author
        self.content = content

    def get_length(self) -> int:
        return len(bytes(self.content, "utf-8"))

    def to_dictionary(self) -> dict[str, object]:
        dictionary = super().to_dictionary()

        dictionary['length'] = self.get_length()
        dictionary['time'] = self.time
        dictionary['author'] = self.author
        dictionary['content'] = self.content
        return dictionary

    @classmethod
    def from_dictionary(cls, dictionary: dict) -> 'Message':
        return cls(dictionary['time'], dictionary['author'], dictionary['content'], dictionary["length"])

def encode_packet(packet: Packet) -> bytes:
    """Encodes a packet ready to send.
    """
    json_string = json.dumps(packet.to_dictionary())
    return json_string.encode()

def decode_packet(data: bytes):
    """Decodes a packet ready to use.
    """
    dictionary = json.loads(data.decode())

    packet = Packet.from_dictionary(dictionary)
    match(packet.name):
        case "Login":
            return Login.from_dictionary(dictionary)

        case "Logout":
            return Logout.from_dictionary(dictionary)

        case "Message":
            return Message.from_dictionary(dictionary)

        case _:
            return Packet("Invalid")
