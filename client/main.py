import view
import player
import boss
import shared.data as data
import sys

# read the username from the cmd args
if (len(sys.argv) > 1):
    username = sys.argv[1]


player = player.Player(data.PlayerData(None, "Player1", 10,1))
boss = boss.Boss(data.BossData("Alien", 1, 100, 100))
view = view.PlayerApp(player, boss)