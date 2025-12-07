import customtkinter as ctk
import tkinter as tk
from player import Player
from boss import Boss
import services
import os
from ctypes import windll
from PIL import Image, ImageTk

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
        favicon = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "Alien.ico"))
        photo = ImageTk.PhotoImage(favicon)
        root.iconphoto(True, photo)
        #root.iconbitmap(photo)

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
                BASE_DIR, "images", "AlienHit.png")),
            tk.PhotoImage(file=os.path.join(
                BASE_DIR, "images", "AlienDead.png"))
        ]

        # Background Canvas for displaying the images
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="blue")
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.canvas.create_rectangle(0, 0, 1, 1, fill="red")
        self.bg_img = tk.PhotoImage(file=os.path.join(
            BASE_DIR, "images", "BackgroundCave.png"))
        self.canvas.create_image(0, 0, image=self.bg_img, anchor="nw")
        self.character = self.canvas.create_image(
            0, 0, image=self.character_frames[0], anchor="nw", tag="character")
        self._update_health_bar(self.canvas)

        # Foreground elements
        # Top bar with level and player count info
        top_bar = ctk.CTkFrame(self, bg_color="transparent", fg_color="transparent", height=40)
        top_bar.pack(fill='x')

        level_label = ctk.CTkLabel(top_bar, text=f"Level: {1}", font=("Lucida Sans", 16))
        level_label.pack(side="left", padx=10, pady=5)

        players_label = ctk.CTkLabel(top_bar, text=f"Players: {1000}", font=("Lucida Sans", 16))
        players_label.pack(side="right", padx=10, pady=5)

        self.boss_health_label = ctk.CTkLabel(top_bar, text=self.app.boss.get_name(), font=("Arial", 22, "bold"))
        self.boss_health_label.place(relx=0.5, rely=0.5, anchor="center")

        # Attack button
        self.attack_button = ctk.CTkButton(self, text="Attack!", bg_color="black",
                               corner_radius=0, command=self._on_damage_input)
        self.attack_button.pack(side="bottom", pady = 20)

        # Do not bind here. GamePage will bind/unbind when shown/hidden so
        # the space key only works while this frame is active.

    def _update_health_bar(self, canvas: tk.Canvas):
        """Destroys the old health bar and creates a new one based on the boss's current health."""
        canvas.delete("healthbar")
        text_id = self.canvas.create_text(450,150, text=f"Health: {self.app.boss.get_health()}", font=("Arial", 22, "bold"), fill="white", width=200, tags="healthbar")
        bbox = self.canvas.bbox(text_id)
        x1, y1, x2, y2 = bbox
        padx = 10
        pady = 5
        health_percent = self.app.boss.get_health() / self.app.boss.get_max_health()
        health_rect_end = x1 - padx + health_percent * (x2 - x1 + 2* padx)
        bg_rect_id = self.canvas.create_rectangle(x1-padx, y1-pady, x2+padx, y2+pady, fill="red", tags="healthbar")
        health_rect_id = self.canvas.create_rectangle(x1-padx, y1-pady, health_rect_end, y2+pady, fill="green2", tags="healthbar")
        border_rect_id = self.canvas.create_rectangle(x1-padx, y1-pady, x2+padx, y2+pady, width=4, tags="healthbar")

        # Fix ordering
        canvas.tag_raise(text_id, health_rect_id)

    def on_show(self):
        """Called when GamePage becomes visible. Bind the space key at root level."""
        try:
            # bind at root so the event is handled regardless of widget focus
            self.root.bind_all("<space>", lambda e: self._on_damage_input())
        except Exception:
            # fallback to frame-local bind
            self.bind("<space>", lambda e: self._on_damage_input())
            self.focus_set()

    def on_hide(self):
        """Called when GamePage is hidden. Unbind the space key to avoid handling while inactive."""
        try:
            self.root.unbind_all("<space>")
        except Exception:
            self.unbind("<space>")

    def _on_damage_input(self):
        """Event callback for when the player inputs damage (presses space or clicks attack button)."""
        if self.app.boss.is_dead():
            return
        print("Damage input")
        self.app.player.deal_damage()
        self.app.boss.receive_damage(10)

        boss_dead = self.app.boss.is_dead()
        # Animate character hit
        self.canvas.itemconfig(self.character, image=self.character_frames[1])
        self.after(100, lambda: self.canvas.itemconfig(
            self.character, image=self.character_frames[2 if boss_dead else 0]), )
        
        # Update boss health label
        self._update_health_bar(self.canvas)

        if self.app.boss.is_dead():
            self.attack_button._state = "disabled"
            self._show_defeated_text()

    def _show_defeated_text(self):
        self.canvas.create_text(304,204, text="Boss Defeated!", font=("Arial", 50, "bold"), fill="black", tags="defeat_text")
        self.canvas.create_text(300,200, text="Boss Defeated!", font=("Arial", 50, "bold"), fill="white", tags="defeat_text")
        self.canvas.create_text(300,260, text="Get ready for the next boss...", font=("Arial", 20, "bold"), fill="white", tags="defeat_text")