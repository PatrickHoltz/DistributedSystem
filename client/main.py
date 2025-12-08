import view
import shared.data as data
import client.controller as client

game_manager = client.GameStateManager()
login_service = client.ConnectionService(game_manager)
view = view.PlayerApp(player, boss)