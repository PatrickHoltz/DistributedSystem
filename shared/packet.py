import json
import struct
from dataclasses import is_dataclass, asdict
from enum import StrEnum
from uuid import uuid4


class PacketTag(StrEnum):
    NONE = "none"
    LOGOUT = "logout"
    ATTACK = "attack"
    PLAYER_GAME_STATE = "player_game_state"
    STRING_MESSAGE = "string_message"
    NEW_MONSTER = "new_monster"
    MONSTER_DEAD = "monster_dead"
    CLIENT_PING = "client_ping"
    CLIENT_PONG = "client_pong"
    SERVER_HELLO = "server_hello"

    # login request
    LOGIN = "login"
    # login reply by the leader
    LOGIN_REPLY = "login_reply"
    # login confirmation by the target server
    LOGIN_CONFIRM = "login_confirm"

    #Bully
    BULLY_ELECTION = "election"
    BULLY_OK = "ok"
    BULLY_COORDINATOR = "coordinator"
    BULLY_LEADER_HEARTBEAT = "leader_heartbeat"

    # Leader communication
    SERVER_HEARTBEAT = "server_heartbeat"


class Packet:
    """Basic packet for client-server communication.
    """

    def __init__(self, content: object | dict, tag: PacketTag = PacketTag.NONE, packet_uuid: str = None,
                 server_uuid: int = -1, length: int = 0):
        """Content must be a dataclass"""
        self.content = content
        self.tag = tag
        self.packet_uuid = str(uuid4()) if packet_uuid is None else packet_uuid
        self.server_uuid = server_uuid

        self._length = length

    def _to_dictionary(self) -> dict[str, object]:
        if not is_dataclass(self.content):
            raise TypeError("Could not encode packet. Packet content must be a dataclass.")
        dictionary = dict()
        dictionary['tag'] = self.tag.value
        dictionary['packet_uuid'] = self.packet_uuid
        dictionary['server_uuid'] = self.server_uuid
        dictionary['content'] = asdict(self.content)
        return dictionary

    def encode(self) -> bytes:
        """Encodes a packet to a byte array ready to send. The returned bytes start with a 4 byte data length.
        """
        json_string = json.dumps(self._to_dictionary())
        json_bytes = json_string.encode()

        # prepend the data length as a 4-byte integer
        data_length = struct.pack('!I', len(json_bytes))
        return data_length + json_bytes

    @classmethod
    def decode(cls, data: bytes) -> 'Packet':
        """Decodes a byte array into a packet ready to use. The content is kept as a dictionary.
        """
        data_length = int.from_bytes(data[0:4])
        dictionary = json.loads(data[4:].decode())
        return cls(dictionary['content'], PacketTag(dictionary["tag"]), str(dictionary["packet_uuid"]),
                   int(dictionary["server_uuid"]), data_length)
