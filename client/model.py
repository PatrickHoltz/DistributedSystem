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
        damage = self.player.damage
        self.boss.receive_damage(damage)


class Player:
    def __init__(self):
        self.username = ""
        self.damage = 0
        self.level = 0
        self.logged_in = False

    def update_state(self, game_state: PlayerData):
        self.username = game_state.username
        self.damage = game_state.damage
        self.level = game_state.level

class Boss:
    def __init__(self):
        self.name = ""
        self.stage = 0
        self.health = 0
        self.max_health = 0

    def receive_damage(self, damage: int):
        self.health -= damage
        if self.is_dead():
            self.health = 0
            print("Boss defeated!")

    def update_state(self, boss_data: BossData):
        self.name = boss_data.name
        self.stage = boss_data.stage
        self.health = boss_data.health
        self.max_health = boss_data.max_health
    
    def set_dead(self):
        self.health = 0

    def is_dead(self) -> bool:
        return self.health <= 0