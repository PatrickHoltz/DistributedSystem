from shared.dtos import *

class Boss:
    def __init__(self, boss_data: BossData):
        self.boss_data = boss_data
        pass

    def receive_damage(self):
        #send damage to server
        pass

    def update_boss_health(self):
        #update boos health
        pass

    def get_boss_health(self):
        return self.boss_data.health