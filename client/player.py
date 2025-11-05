from shared.utils import *

class Player:
    def __init__(self, game_state: GameState):
        self.id = None
        self.game_state = game_state
        pass


    def deal_damage(self):
        # send damage to server
        pass

    def get_player_data(self) -> PlayerData:
        return self.game_state.players[self.id]