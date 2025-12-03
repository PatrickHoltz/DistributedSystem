import customtkinter as ctk
from player import Player
from boss import Boss


class PlayerView:
    '''
    UI class for the player. Allows a player to interact with the game.
    '''
    def __init__(self, player: Player, boss: Boss):
        self.player = player
        self.boss = boss
        self.__start()



    def __start(self):


        root = ctk.CTk()
        root.title("Kill the Boss")
        root.geometry("600x400")

        level_label = ctk.CTkLabel(root, text=f"Level: {1}")
        level_label.pack()

        button = ctk.CTkButton(root, text="Deal Damage", corner_radius= 4, command=self.__on_damage_input)
        button.bind()
        button.pack()



        self.boss_health_label = ctk.CTkLabel(root, text=f"Health: {self.boss.get_boss_health()}")
        self.boss_health_label.pack()

        root.bind("<space>", lambda event : self.__on_damage_input())

        root.mainloop()


    def __on_damage_input(self):
            self.player.deal_damage()
            print("Damage input")

    def __change_label_color(self):
        if self.boss.get_boss_health() > 200:
            self.boss_health_label.configure(text_color="green")
        else:
            self.boss_health_label.configure(text_color="red")
