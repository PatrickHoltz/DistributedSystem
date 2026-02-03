from shared.data import PlayerData, PlayerGameStateData, MonsterData


class ClientGameState:
    def __init__(self):
        self.player = Player()
        self.monster = Monster()
        self.player_count = 0

    def update(self, game_state: PlayerGameStateData):
        self.player.update_state(game_state.player)
        self.monster.update(game_state.monster)
        self.player_count = game_state.player_count
    
    def attack_monster(self):
        damage = self.player.damage
        self.monster.receive_damage(damage)


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

class Monster:
    def __init__(self):
        self.name = ""
        self.stage = 0
        self.health = 0
        self.max_health = 0

    def receive_damage(self, damage: int):
        self.health -= damage
        if self.is_dead():
            self.health = 0
            print("Monster defeated!")

    def update(self, monster_data: MonsterData):
        self.name = monster_data.name
        self.stage = monster_data.stage
        self.health = monster_data.health
        self.max_health = monster_data.max_health
    
    def set_dead(self):
        self.health = 0

    def is_dead(self) -> bool:
        return self.health <= 0