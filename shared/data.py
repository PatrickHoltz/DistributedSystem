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

    @classmethod
    def from_dict(cls, data):
        players = {username: PlayerData(**pdata) for username, pdata in data['players'].items()}
        boss_data = BossData(**data['boss'])
        return cls(players=players, boss=boss_data)

@dataclass
class PlayerGameState:
    '''Snapshot of the game state relevant to a specific player.'''
    boss: BossData
    player: PlayerData
    player_count: int

    @classmethod
    def from_dict(cls, data):
        boss_data = BossData(**data['boss'])
        player_data = PlayerData(**data['player'])
        player_count = data['player_count']
        return cls(boss=boss_data, player=player_data, player_count=player_count)

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