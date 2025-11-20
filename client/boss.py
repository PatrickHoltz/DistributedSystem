from shared.utils import *

class Boss:
    def __init__(self, game_state: GameState):
        self.game_state = game_state
        pass

    def receive_damage(self):
        #send damage to server
        pass

    def update_boss_health(self):
        #update boos health
        pass

    def get_boss_health(self):
        return self.game_state.boss_stats.health