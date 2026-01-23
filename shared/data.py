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
class GossipDamage:
    boss_id: str
    server_uuid: str
    total_damage: int

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
    server_port: int
    game_state: PlayerGameStateData

    @classmethod
    def from_dict(cls, data: dict):
        server_port = data['server_port']
        game_state = PlayerGameStateData.from_dict(data['game_state']) if data['game_state'] else None
        return cls(server_port=server_port, game_state=game_state)

@dataclass
class LoginData:
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

@dataclass
class LeaderHeartbeat:
    leader_uuid: str

