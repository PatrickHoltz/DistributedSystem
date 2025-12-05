from shared.data import *

class Boss:
    def __init__(self, boss_data: BossData):
        self._name = boss_data.name
        self._health = boss_data.health
        self._max_health = boss_data.max_health

    def receive_damage(self, damage: int):
        self._health -= damage
        if self.is_dead():
            self._health = 0
            print("Boss defeated!")
        #send damage to server

    def update_state(self, boss_data: BossData):
        self._name = boss_data.name
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