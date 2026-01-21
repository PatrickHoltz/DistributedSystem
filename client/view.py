from __future__ import annotations

import os
import random
import tkinter as tk
import uuid
from ctypes import windll
from typing import cast

import customtkinter as ctk
from PIL import Image, ImageTk

from client.events import UIEventDispatcher, Events
from model import ClientGameState

# with Windows set the script to be dpi aware before calling Tk()
windll.shcore.SetProcessDpiAwareness(1)


class PlayerApp:
    """
    UI class for the player. Allows a player to interact with the game.
    """

    def __init__(self, root: tk.Tk, dispatcher: UIEventDispatcher):
        self.frames: dict[type, ctk.CTkFrame] = {}
        self.root = root
        self.dispatcher = dispatcher

        self._setup()

    def _setup(self):
        # Event subscriptions
        self.dispatcher.subscribe(Events.LOGGED_IN, self.on_logged_in)

        # Window setup
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)

        self.root.title("Kill the Boss")
        self.root.geometry("600x400")
        self.root.geometry(f"{600}x{400}")
        self.root.resizable(False, False)

        favicon = Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "Alien.ico"))
        photo = ImageTk.PhotoImage(favicon)
        self.root.iconphoto(True, photo)

        # Setup frames
        for Page in (LoginPage, GamePage):
            frame = Page(self.root, self)
            self.frames[Page] = frame
            frame.place(x=0, y=0, relwidth=1, relheight=1)

        self.show_frame(LoginPage)

    def show_frame(self, frame_to_show: type[ctk.CTkFrame]):
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

    def on_logged_in(self, game_state: ClientGameState):
        self.show_frame(GamePage)
        game_page: GamePage = cast(GamePage, self.frames[GamePage])
        game_page.update_frame(game_state)


class LoginPage(ctk.CTkFrame):
    def __init__(self, master, app: PlayerApp, width=200, height=200, corner_radius=None, border_width=None,
                 bg_color="transparent", fg_color=None, border_color=None, background_corner_colors=None,
                 overwrite_preferred_drawing_method=None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color,
                         border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)
        self.app = app

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
        self.app.dispatcher.emit(Events.LOGIN_CLICKED, username)


class GamePage(ctk.CTkFrame):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, master, app: PlayerApp, width=200, height=200, corner_radius=None, border_width=None,
                 bg_color="transparent", fg_color=None, border_color=None, background_corner_colors=None,
                 overwrite_preferred_drawing_method=None, **kwargs):
        super().__init__(master, width, height, corner_radius, border_width, bg_color, fg_color,
                         border_color, background_corner_colors, overwrite_preferred_drawing_method, **kwargs)
        # keep a reference to the root/master so we can bind/unbind on show/hide
        self.character_frames = [
            tk.PhotoImage(file=os.path.join(
                self.BASE_DIR, "images", "Alien.png")),
            tk.PhotoImage(file=os.path.join(
                self.BASE_DIR, "images", "AlienHit.png")),
            tk.PhotoImage(file=os.path.join(
                self.BASE_DIR, "images", "AlienDead.png"))
        ]

        self.root = master
        self.app = app
        self.boss_defeated = False
        self.is_animating_boss = False
        self.active_damage_numbers: dict[str, int] = {}

        self.app.dispatcher.subscribe(Events.UPDATE_GAME_STATE, self.update_frame)
        self.app.dispatcher.subscribe(Events.NEW_BOSS, self._on_new_boss)

        # Background Canvas for displaying the images
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="blue")
        self.canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.canvas.create_rectangle(0, 0, 1, 1, fill="red")
        self.bg_img = tk.PhotoImage(file=os.path.join(
            self.BASE_DIR, "images", "BackgroundCave.png"))
        self.canvas.create_image(0, 0, image=self.bg_img, anchor="nw")
        self.character = self.canvas.create_image(
            0, 0, image=self.character_frames[0], anchor="nw", tag="character")
        self._update_health_bar()

        self._render_damage_number(30)

        # Foreground elements
        # Top bar with level and player count info
        top_bar = ctk.CTkFrame(self, bg_color="transparent", fg_color="transparent", height=40)
        top_bar.pack(fill='x')

        self.level_label = ctk.CTkLabel(top_bar, text=f"Level: {1}", font=("Lucida Sans", 16))
        self.level_label.pack(side="left", padx=10, pady=5)

        self.players_label = ctk.CTkLabel(top_bar, text=f"Players: {1000}", font=("Lucida Sans", 16))
        self.players_label.pack(side="right", padx=10, pady=5)

        self.boss_name_label = ctk.CTkLabel(top_bar, text="-", font=("Arial", 22, "bold"))
        self.boss_name_label.place(relx=0.5, rely=0.5, anchor="center")

        # Attack button
        self.attack_button = ctk.CTkButton(self, text="Attack!", bg_color="black",
                                           corner_radius=0, command=self._on_attack_input)
        self.attack_button.pack(side="bottom", pady=20)

        # Logout button
        self.logout_button = ctk.CTkButton(self, text="Logout", bg_color="black", corner_radius=0, width=60,
                                           fg_color="red4", hover_color="red3", command=self._on_logout_pressed)
        self.logout_button.place(anchor="se", relx=1.0, rely=1.0)

    def update_frame(self, game_state: ClientGameState, damage_numbers=None):
        """Updates the GamePage with the provided game state."""
        if self.boss_defeated:
            return

        self.level_label.configure(text=f"Level: {game_state.player.level}")
        self.players_label.configure(text=f"Players: {game_state.player_count}")
        self.boss_name_label.configure(text=game_state.boss.name)
        self._update_health_bar(game_state.boss.health, game_state.boss.max_health)

        if damage_numbers:
            for n in damage_numbers:
                self._render_damage_number(n)

        if not self.is_animating_boss:
            self.canvas.itemconfig(self.character, image=self.character_frames[2 if game_state.boss.is_dead() else 0])

        if game_state.boss.is_dead():
            self._display_defeated_screen()
            self.boss_defeated = True


    def _update_health_bar(self, health=100, max_health=100):
        """Destroys the old health bar and creates a new one based on the boss's current health."""
        self.canvas.delete("healthbar")
        text_id = self.canvas.create_text(450, 150, text=f"Health: {health}", font=("Arial", 22, "bold"), fill="white",
                                          width=200, tags="healthbar")
        bbox = self.canvas.bbox(text_id)
        x1, y1, x2, y2 = bbox
        padx = 10
        pady = 5
        health_percent = health / max_health
        health_rect_end = x1 - padx + health_percent * (x2 - x1 + 2 * padx)
        self.canvas.create_rectangle(x1 - padx, y1 - pady, x2 + padx, y2 + pady, fill="red", tags="healthbar")
        health_rect_id = self.canvas.create_rectangle(x1 - padx, y1 - pady, health_rect_end, y2 + pady, fill="green2",
                                                      tags="healthbar")
        self.canvas.create_rectangle(x1 - padx, y1 - pady, x2 + padx, y2 + pady, width=4, tags="healthbar")

        # Fix ordering
        self.canvas.tag_raise(text_id, health_rect_id)

    def _render_damage_number(self, damage: int):
        number_id: str = str(uuid.uuid4())
        x = random.randrange(80, 300)
        y = random.randrange(80, 250)
        self.canvas.create_text(x, y, text=f"{damage}", font=("Arial", 30), fill="orange red", tags=number_id)
        self._number_animation(number_id)

    def _number_animation(self, number_id: str):
        n_frames = 40
        for i in range(1, n_frames):
            size = n_frames - i
            self.after(i * 50,
                       lambda ida, s: self.canvas.itemconfig(ida, font=("Arial", s)),
                       number_id, size)
        self.after(50*n_frames, lambda: self.canvas.delete(number_id))

    def test(self, number_id: str, i: int = 1):
        print("hello")
        self.canvas.itemconfig(number_id, font=("Arial", 20 + 2*i))

    def on_show(self):
        """Called when GamePage becomes visible. Bind the space key at root level."""
        try:
            # bind at root so the event is handled regardless of widget focus
            self.root.bind_all("<space>", lambda e: self._on_attack_input())
        except Exception:
            # fallback to frame-local bind
            self.bind("<space>", lambda e: self._on_attack_input())
            self.focus_set()

    def on_hide(self):
        """Called when GamePage is hidden. Unbind the space key to avoid handling while inactive."""
        try:
            self.root.unbind_all("<space>")
        except Exception:
            self.unbind("<space>")

    def _on_attack_input(self):
        """Event callback for when the player inputs damage (presses space or clicks attack button)."""
        if self.boss_defeated:
            return
        print("Attack input received.")
        self.app.dispatcher.emit(Events.ATTACK_CLICKED)

        # Animate character hit
        self.canvas.itemconfig(self.character, image=self.character_frames[1])
        self.is_animating_boss = True
        self.after(100, self._attack_after)

    def _on_logout_pressed(self):
        """Event callback for when the player clicks the logout button."""

        self.app.show_frame(LoginPage)
        self.app.dispatcher.emit(Events.LOGOUT_CLICKED)

    def _attack_after(self):
        self.is_animating_boss = False
        self.canvas.itemconfig(self.character, image=self.character_frames[2 if self.boss_defeated else 0])

    def _display_defeated_screen(self):
        self.canvas.create_text(304, 204, text="Boss Defeated!", font=("Arial", 50, "bold"), fill="black",
                                tags="defeat_text")
        self.canvas.create_text(300, 200, text="Boss Defeated!", font=("Arial", 50, "bold"), fill="white",
                                tags="defeat_text")
        self.canvas.create_text(300, 260, text="Get ready for the next boss...", font=("Arial", 20, "bold"),
                                fill="white", tags="defeat_text")
        self.canvas.delete("healthbar")
        self.attack_button.configure(state=tk.DISABLED)
        self.canvas.itemconfig(self.character, image=self.character_frames[2])

    def _hide_defeated_screen(self):
        self.canvas.delete("defeat_text")
        self.attack_button.configure(state=tk.NORMAL)

    def _on_new_boss(self, game_state: ClientGameState):
        self.boss_defeated = True
        self._display_defeated_screen()
        self.after(3000, lambda: self._init_new_boss(game_state))

    def _init_new_boss(self, game_state: ClientGameState):
        self._hide_defeated_screen()
        self.boss_defeated = False
        self.update_frame(game_state)
