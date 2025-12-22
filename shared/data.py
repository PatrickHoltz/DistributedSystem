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
class GameState:
    players: dict[str, PlayerData]
    boss: BossData

@dataclass
class PlayerGameState:
    '''Snapshot of the game state relevant to a specific player.'''
    boss: BossData
    player: PlayerData
    player_count: int

@dataclass
class LoginData:
    username: str

@dataclass
class StringMessage:
    message: str

@dataclass
class AttackData:
    username: str
    damage: int

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

@dataclass
class LeaderHeartbeat:
    leader_uuid: str