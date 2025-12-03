import view
import player
import boss
import shared.dtos as dtos
from shared.socket import TCPSocket
import shared.packet as packet

player = player.Player(dtos.PlayerData(None, "Player1", 10,1))
boss = boss.Boss(dtos.BossData("Boss1", 100))
view = view.PlayerView(player, boss)

login_data = dtos.LoginData("123456", "Alice")
packet = packet.Packet(login_data)
socket = TCPSocket(packet, "127.0.0.1", 10001)
socket.start()
socket.join()
