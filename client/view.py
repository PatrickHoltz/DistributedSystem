import customtkinter as ctk
from player import Player
from boss import Boss
import services


class PlayerApp:
    '''
    UI class for the player. Allows a player to interact with the game.
    '''
    def __init__(self, player: Player, boss: Boss):
        self.player = player
        self.boss = boss
        self.frames: dict[type, ctk.CTkFrame] = {}
        self.__start()


    def __start(self):
        root = ctk.CTk()
        root.title("Kill the Boss")
        root.geometry("600x400")

        for Page in (LoginPage, GamePage):
             frame = Page(root, self)
             self.frames[Page] = frame
             frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.show_frame(LoginPage)
        root.mainloop()

    def show_frame(self, frame_to_show: ctk.CTkFrame):
        frame = self.frames[frame_to_show]
        frame.tkraise()

class LoginPage(ctk.CTkFrame):
    def __init__(self, master, app: PlayerApp, width = 200, height = 200, corner_radius = None, border_width = None, bg_color = "transparent", fg_color = None, border_color = None, background_corner_colors = None, overwrite_preferred_drawing_method = None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color, border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)

        center_frame = ctk.CTkFrame(self, fg_color="transparent")
        center_frame.pack(expand=True)

        label = ctk.CTkLabel(center_frame, text="Welcome to Kill the Boss. Please login.")
        label.pack()

        self.username_input = ctk.CTkEntry(center_frame, placeholder_text="Enter username")
        self.username_input.pack(pady=10)

        button = ctk.CTkButton(center_frame, text="Login", command=self.login)
        button.pack()
    
    def login(self):
        username = self.username_input.get()
        services.login_service.login(username)
        

class GamePage(ctk.CTkFrame):
    def __init__(self, master, app: PlayerApp, width = 200, height = 200, corner_radius = None, border_width = None, bg_color = "transparent", fg_color = None, border_color = None, background_corner_colors = None, overwrite_preferred_drawing_method = None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color, border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)
        self.app = app
        level_label = ctk.CTkLabel(self, text=f"Level: {1}")
        level_label.pack()

        button = ctk.CTkButton(self, text="Deal Damage", corner_radius= 4, command=self.__on_damage_input)
        button.bind()
        button.pack()



        self.boss_health_label = ctk.CTkLabel(self, text=f"Health: {app.boss.get_boss_health()}")
        self.boss_health_label.pack()

        self.bind("<space>", lambda event : self.__on_damage_input())

    def __on_damage_input(self):
        self.app.player.deal_damage()
        print("Damage input")

    def __change_label_color(self):
        if self.app.boss.get_boss_health() > 200:
            self.boss_health_label.configure(text_color="green")
        else:
            self.boss_health_label.configure(text_color="red")

