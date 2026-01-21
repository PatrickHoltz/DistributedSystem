import tkinter as tk

import view
import controller
import model
from client.events import UIEventDispatcher


if __name__ == "__main__":
    tk_root = tk.Tk()

    dispatcher = UIEventDispatcher(tk_root)

    state_manager = model.ClientGameState()
    game_controller = controller.GameController(state_manager, dispatcher)
    player_app = view.PlayerApp(tk_root, dispatcher)


    dispatcher.start()

    try:
        tk_root.mainloop()
    except (KeyboardInterrupt, SystemExit):
        print("Stopping gracefully...")
        tk_root.destroy()