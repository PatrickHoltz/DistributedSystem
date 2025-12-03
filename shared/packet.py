"""Module containing all the commonly shared classes and functions between server and client.
"""

import json
from dataclasses import asdict


SERVER_ADDR = '127.0.0.1'
SERVER_PORT = 10002

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