import customtkinter as ctk
import tkinter as tk
from player import Player
from boss import Boss
import services
import os
from ctypes import windll

# with Windows set the script to be dpi aware before calling Tk()
windll.shcore.SetProcessDpiAwareness(1)


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
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)

        # we use Tk since Ctk adds unwanted styling to the root
        root = tk.Tk()
        root.title("Kill the Boss")
        root.geometry("600x400")
        root.geometry(f"{600}x{400}")
        root.resizable(False, False)
        scaleFactor = windll.shcore.GetScaleFactorForDevice(0) / 100
        root.tk.call('tk', 'scaling', scaleFactor)

        for Page in (LoginPage, GamePage):
            frame = Page(root, self)
            self.frames[Page] = frame
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.show_frame(GamePage)
        root.mainloop()

    def show_frame(self, frame_to_show: ctk.CTkFrame):
        # call on_hide on previous frame if implemented
        try:
            if hasattr(self, "current_frame") and self.current_frame and hasattr(self.current_frame, "on_hide"):
                self.current_frame.on_hide()
        except Exception:
            pass

        new_frame = self.frames[frame_to_show]
        new_frame.tkraise()

        # call on_show on the new frame if implemented
        try:
            if hasattr(new_frame, "on_show"):
                new_frame.on_show()
        except Exception:
            pass

        # track current frame for future hide calls
        self.current_frame = new_frame


class LoginPage(ctk.CTkFrame):
    def __init__(self, master, app: PlayerApp, width=200, height=200, corner_radius=None, border_width=None, bg_color="transparent", fg_color=None, border_color=None, background_corner_colors=None, overwrite_preferred_drawing_method=None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color,
                         border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)
        center_frame = ctk.CTkFrame(self, fg_color="transparent")
        center_frame.pack(expand=True)

        label = ctk.CTkLabel(
            center_frame, text="Welcome to Kill the Boss. Please login.")
        label.pack()

        self.username_input = ctk.CTkEntry(
            center_frame, placeholder_text="Enter username")
        self.username_input.pack(pady=10)

        button = ctk.CTkButton(center_frame, text="Login", command=self.login)
        button.pack()

    def login(self):
        username = self.username_input.get()
        services.login_service.login(username)


class GamePage(ctk.CTkFrame):
    def __init__(self, master, app: PlayerApp, width=200, height=200, corner_radius=None, border_width=None, bg_color="transparent", fg_color=None, border_color=None, background_corner_colors=None, overwrite_preferred_drawing_method=None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color,
                         border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)
        self.app = app
        # keep a reference to the root/master so we can bind/unbind on show/hide
        self.root = master
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))

        self.character_frames = [
            tk.PhotoImage(file=os.path.join(
                BASE_DIR, "images", "Alien.png")),
            tk.PhotoImage(file=os.path.join(
                BASE_DIR, "images", "AlienHit.png"))
        ]

        # Background Canvas for displaying the images
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="blue")
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.canvas.create_rectangle(0, 0, 1, 1, fill="red")
        self.bg_img = tk.PhotoImage(file=os.path.join(
            BASE_DIR, "images", "BackgroundCave.png"))
        self.canvas.create_image(0, 0, image=self.bg_img, anchor="nw")
        self.character = self.canvas.create_image(
            0, 0, image=self.character_frames[0], anchor="nw")

        # Foreground elements
        level_label = ctk.CTkLabel(self, text=f"Level: {1}")
        level_label.pack()

        self.boss_health_label = HealthBar(
            self, text=f"Health: {app.boss.get_boss_health()}")
        self.boss_health_label.pack(side="top", pady=10, anchor="n")

        button = ctk.CTkButton(self, text="Attack!",
                               corner_radius=0, command=self.__on_damage_input)
        button.pack(side="bottom", pady = 30)

        # Do not bind here. GamePage will bind/unbind when shown/hidden so
        # the space key only works while this frame is active.

    def on_show(self):
        """Called when GamePage becomes visible. Bind the space key at root level."""
        try:
            # bind at root so the event is handled regardless of widget focus
            self.root.bind_all("<space>", lambda e: self.__on_damage_input())
        except Exception:
            # fallback to frame-local bind
            self.bind("<space>", lambda e: self.__on_damage_input())
            self.focus_set()

    def on_hide(self):
        """Called when GamePage is hidden. Unbind the space key to avoid handling while inactive."""
        try:
            self.root.unbind_all("<space>")
        except Exception:
            self.unbind("<space>")

    def __on_damage_input(self):
        self.app.player.deal_damage()
        self.canvas.itemconfig(self.character, image=self.character_frames[1])
        self.after(100, lambda: self.canvas.itemconfig(
            self.character, image=self.character_frames[0]))

        print("Damage input")

    def __change_label_color(self):
        if self.app.boss.get_boss_health() > 200:
            self.boss_health_label.configure(text_color="green")
        else:
            self.boss_health_label.configure(text_color="red")


class HealthBar(ctk.CTkLabel):
    def __init__(self, master= None, text=""):
        super().__init__(master, text=text, bg_color="transparent", fg_color="transparent", padx = 10, font=("Arial", 30, "bold"))
