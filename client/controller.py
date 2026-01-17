from __future__ import annotations

from client.events import UIEventDispatcher, Events
from shared.data import *
from shared.sockets import Packet, PacketTag, BroadcastSocket, TCPClientConnection
import threading
from typing import Optional
from model import ClientGameState


class LoginService:
    """Service to send login requests to the server"""

    def __init__(self, game_controller: 'GameController'):
        self._game_controller = game_controller
        self._game_controller.dispatcher.subscribe(Events.LOGIN_CLICKED, self.login)

    def login(self, username: str):
        """Sends a login broadcast and waits for a response to update the game state."""

        print("Logging in...")
        login_data = LoginData(username)
        packet = Packet(login_data, tag=PacketTag.LOGIN)
        login_broadcast = BroadcastSocket(packet, self._handle_login_response, self._handle_login_timeout)
        login_broadcast.start()
        login_broadcast.join()

    def _handle_login_response(self, packet: Packet, address: tuple[str, int]):
        """Called when a login response is received. Updates the game state and starts a TCP connection with the responder."""

        if packet.tag == PacketTag.LOGIN_REPLY:
            try:
                login_reply = LoginReplyData.from_dict(packet.content)
                username = login_reply.game_state.player.username
                self._game_controller.on_logged_in(username, address, login_reply)
            except TypeError as e:
                print("Invalid game state received.", e)
    
    def _handle_login_timeout(self):
        print("Login failed. Server did not respond to login request.")


class ConnectionService(TCPClientConnection):
    """Connection service that composes a TCPClientConnection and processes incoming packets."""

    def __init__(self, address: tuple[str, int], username: str, client_game_state: ClientGameState, dispatcher: UIEventDispatcher):
        super().__init__(address)
        self.dispatcher = dispatcher
        self._client_game_state = client_game_state
        self._username: str = username

    def _handle_packet(self, packet: Packet):
        try:
            # handle the same tags as before
            if packet.tag == PacketTag.CLIENT_PING:
                pong = Packet(StringMessage("pong"), tag=PacketTag.CLIENT_PONG)
                self.send(pong)
                return

            if packet.tag == PacketTag.CLIENT_PONG:
                return

            if packet.tag == PacketTag.PLAYER_GAME_STATE:
                game_state = PlayerGameStateData.from_dict(packet.content)
                self._client_game_state.update(game_state)
                self.dispatcher.emit(Events.UPDATE_GAME_STATE, self._client_game_state)
                return

            if packet.tag == PacketTag.NEW_BOSS:
                typed_packet = TCPClientConnection.get_typed_packet(packet, BossData)
                if typed_packet:
                    self._client_game_state.boss.update(typed_packet.content)
                    self.dispatcher.emit(Events.NEW_BOSS)
                    self.dispatcher.emit(Events.NEW_BOSS, self._client_game_state)
                return

            if packet.tag == PacketTag.BOSS_DEAD:
                # assume content is a simple string
                self._client_game_state.boss.set_dead()
                return

            print(f"Unknown packet tag {packet.tag} received. Aborting packet.")

        except Exception as e:
            print("Error handling packet:", e)

    def send_attack(self, damage: int):
        attack_data = AttackData(username=self._username, damage=damage)
        packet = Packet(attack_data, tag=PacketTag.ATTACK)
        self.send(packet)

    def send_logout(self):
        logout_data = LoginData(self._username)
        packet = Packet(logout_data, tag=PacketTag.LOGOUT)
        self.send(packet)
        print("You are logged out now.")


class GameController:
    def __init__(self, client_game_state: ClientGameState, dispatcher: UIEventDispatcher):
        self.dispatcher = dispatcher
        self.client_game_state = client_game_state
        self.login_service = LoginService(self)
        self._connection_service: Optional[ConnectionService] = None
        self.dispatcher.subscribe(Events.ATTACK_CLICKED, self.on_attack_clicked)

    def on_logged_in(self, username: str, address: tuple[str, int], login_reply: LoginReplyData):
        if self._connection_service:
            self._connection_service.stop()
        print(f"You are now logged in as {username}")

        connect_to_address = (address[0], login_reply.server_port)
        self._connection_service = ConnectionService(connect_to_address, username, self.client_game_state, self.dispatcher)
        self._connection_service.start()
        self.client_game_state.update(login_reply.game_state)
        self.dispatcher.emit(Events.LOGGED_IN, self.client_game_state)

    def on_attack_clicked(self):
        damage = self.client_game_state.player.damage
        self.client_game_state.attack_boss()
        self._connection_service.send_attack(damage)
        self.dispatcher.emit(Events.UPDATE_GAME_STATE, self.client_game_state)
        return self.client_game_state.boss

    def on_logout(self):
        if self._connection_service:
            self._connection_service.send_logout()
        self.client_game_state.player.logged_in = False
        self.dispatcher.emit(Events.LOGGED_OUT)
