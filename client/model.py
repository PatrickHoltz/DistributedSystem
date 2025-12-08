from shared.data import PlayerData, BossData, PlayerGameState

class GameStateManager:
    def __init__(self):
        self.player = Player()
        self.boss = Boss()
        self.player_count = 0

    def update_game_state(self, game_state: PlayerGameState):
        self.player.update_state(game_state.player)
        self.boss.update_state(game_state.boss)
        self.player_count = game_state.player_count
    
    def attack_boss(self):
        damage = self.player._damage
        self.boss.receive_damage(damage)


class Player:
    def __init__(self):
        self._username = ""
        self._damage = 0
        self._level = 0
        self._logged_in = False

    def update_state(self, game_state: PlayerData):
        self._username = game_state.username
        self._damage = game_state.damage
        self._level = game_state.level

class Boss:
    def __init__(self):
        self._name = ""
        self._stage = 0
        self._health = 0
        self._max_health = 0

    def receive_damage(self, damage: int):
        self._health -= damage
        if self.is_dead():
            self._health = 0
            print("Boss defeated!")

    def update_state(self, boss_data: BossData):
        self._name = boss_data.name
        self._stage = boss_data.stage
        self._health = boss_data.health
        self._max_health = boss_data.max_health

    def get_health(self):
        return self._health
    
    def get_max_health(self):
        return self._max_health
    
    def get_name(self):
        return self._name
    
    def is_dead(self) -> bool:
        return self._health <= 0