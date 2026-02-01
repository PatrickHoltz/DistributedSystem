from dataclasses import dataclass

@dataclass
class PlayerData:
    username: str
    damage: int
    level: int
    online: bool
    last_seen_ts: float = 0.0
    #damageDone: int

@dataclass
class MonsterData:
    name: str
    stage: int
    health: int
    max_health: int

@dataclass
class GameStateData:
    players: dict[str, PlayerData]
    monster: MonsterData

    @classmethod
    def from_dict(cls, data: dict):
        players = {username: PlayerData(**pdata) for username, pdata in data['players'].items()}
        monster_data = MonsterData(**data['monster'])
        return cls(players=players, monster=monster_data)

@dataclass
class PlayerGameStateData:
    """Snapshot of the game state relevant to a specific player."""
    monster: MonsterData
    player: PlayerData
    player_count: int
    latest_damages: list[int]

    @classmethod
    def from_dict(cls, data: dict):
        monster_data = MonsterData(**data['monster'])
        player_data = PlayerData(**data['player'])
        player_count = data['player_count']
        latest_damages = data['latest_damages']
        return cls(monster=monster_data, player=player_data, player_count=player_count, latest_damages=latest_damages)

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
    udp_port: int
    tcp_port: int

@dataclass
class ServerState:
    server_info: ServerInfo
    last_seen: float

@dataclass
class ClientInfo:
    username: str
    ip: str
    port: int

@dataclass
class GossipBossSync:
    boss_id: str
    leader_uuid: str
    boss: BossData

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            boss_id=data["boss_id"],
            leader_uuid=data["leader_uuid"],
            boss=BossData(**data["boss"]),
        )

@dataclass
class PlayerStats:
    player_count: int
    players: dict[str, PlayerData]

@dataclass
class GossipPlayerStats:
    server_uuid: str
    seq: int
    stats: PlayerStats