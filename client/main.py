import view
import controller
import model

state_manager = model.GameStateManager()
game_controller = controller.GameController()
player_app = view.PlayerApp(game_controller)