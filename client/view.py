import tkinter as tk
import player


class PlayerView:
    '''
    UI class for the player. Allows a player to interact with the game.
    '''
    def __init__(self, player: player.Player):
        self.player = player
        self.__start()
    
    def __start(self):
        root = tk.Tk()
        root.title("Kill the Boss")
        root.geometry("600x400")

        level_label = tk.Label(text=f"Level: {1}")
        level_label.pack()

        button = tk.Button(text="Deal Damage", command=self.__on_damage_input)
        button.bind()
        button.pack()

        boss_health_label = tk.Label(text=f"Health: {1000}")
        boss_health_label.pack()

        root.bind("<space>", lambda event : self.__on_damage_input())

        root.mainloop()

    def __on_damage_input(self):
            self.player.deal_damage()
            print("Damage input")