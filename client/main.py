import view
import player
from shared.utils import PlayerData

player = player.Player(PlayerData(None, "Player1", 10,1))
view = view.PlayerView(player)