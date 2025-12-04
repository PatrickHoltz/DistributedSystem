from uuid import uuid4 as uuid
from dataclasses import dataclass

@dataclass
class PlayerData:
    id: uuid
    name: str
    damage: int
    level: int

@dataclass
class BossData:
    name: str
    health: int

@dataclass
class GameState:
    players: list[PlayerData]
    boss_stats: BossData

@dataclass
class LoginData:
    username: str

@dataclass
class StringMessage:
    message: str