from dataclasses import dataclass

@dataclass
class PlayerData:
    username: str
    damage: int
    level: int
    online: bool

@dataclass
class BossData:
    name: str
    stage: int
    health: int
    max_health: int

@dataclass
class GameStateData:
    players: dict[str, PlayerData]
    boss: BossData

    @classmethod
    def from_dict(cls, data: dict):
        players = {username: PlayerData(**pdata) for username, pdata in data['players'].items()}
        boss_data = BossData(**data['boss'])
        return cls(players=players, boss=boss_data)

@dataclass
class PlayerGameStateData:
    """Snapshot of the game state relevant to a specific player."""
    boss: BossData
    player: PlayerData
    player_count: int
    latest_damages: list[int]

    @classmethod
    def from_dict(cls, data: dict):
        boss_data = BossData(**data['boss'])
        player_data = PlayerData(**data['player'])
        player_count = data['player_count']
        latest_damages = data['latest_damages']
        return cls(boss=boss_data, player=player_data, player_count=player_count, latest_damages=latest_damages)

@dataclass
class LoginReplyData:
    """Data sent back to client after a login broadcast."""
    server_ip: str
    server_port: int
    username: str

@dataclass
class LoginData:
    """Data sent by a client for a login request"""
    username: str

@dataclass
class StringMessage:
    message: str

@dataclass
class AttackData:
    username: str

@dataclass
class ServerHello:
    uuid: str

@dataclass
class ElectionMessage:
    candidate_uuid: str

@dataclass
class OkMessage:
    responder_uuid: str

@dataclass
class CoordinatorMessage:
    leader_uuid: str
    leader_ip: str
    leader_port: int

@dataclass
class LeaderHeartbeat:
    leader_uuid: str
    leader_ip: str
    leader_port: int

@dataclass
class ServerInfo:
    server_uuid: str
    occupancy: int
    ip: str
    listening_port: int

@dataclass
class ClientInfo:
    username: str
    ip: str
    port: int