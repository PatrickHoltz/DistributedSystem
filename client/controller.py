from __future__ import annotations

from typing import Optional

from client.events import UIEventDispatcher, Events
from model import ClientGameState
from shared.data import *
from shared.sockets import BroadcastSocket, TCPClientConnection, SocketUtils
from shared.packet import PacketTag, Packet
from shared.utils import Debug


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

    def _handle_login_response(self, packet: Packet, _address: tuple[str, int]):
        """Called when a login response is received. Updates the game state and starts a TCP connection with the responder."""

        if packet.tag == PacketTag.LOGIN_REPLY:
            try:
                login_reply = LoginReplyData(**packet.content)
                self._game_controller.on_logged_in(login_reply)
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
            match packet.tag:
                case PacketTag.LOGIN_CONFIRM:
                    game_state = PlayerGameStateData.from_dict(packet.content)
                    self._client_game_state.update(game_state)
                    print(f"You are now logged in as {self._username}")
                    self.dispatcher.emit(Events.LOGGED_IN, self._client_game_state)

                # handle the same tags as before
                case PacketTag.CLIENT_PING:
                    pong = Packet(StringMessage("pong"), tag=PacketTag.CLIENT_PONG)
                    self.send(pong)

                case PacketTag.CLIENT_PONG:
                    Debug.log("Pong received", "CLIENT")

                case PacketTag.PLAYER_GAME_STATE:
                    game_state = PlayerGameStateData.from_dict(packet.content)
                    self._client_game_state.update(game_state)
                    self.dispatcher.emit(Events.UPDATE_GAME_STATE, self._client_game_state, game_state.latest_damages)

                case PacketTag.NEW_BOSS:
                    typed_packet = SocketUtils.get_typed_packet(packet, BossData)
                    if typed_packet:
                        print("New boss received:", typed_packet.content)
                        self._client_game_state.boss.update(typed_packet.content)
                        self.dispatcher.emit(Events.NEW_BOSS, self._client_game_state)

                case PacketTag.BOSS_DEAD:
                    # assume content is a simple string
                    self._client_game_state.boss.set_dead()

                case _:
                    print(f"Unknown packet tag {packet.tag} received. Aborting packet.")

        except Exception as e:
            print("Error handling packet:", e)

    def send_attack(self):
        attack_data = AttackData(username=self._username)
        packet = Packet(attack_data, tag=PacketTag.ATTACK)
        self.send(packet)

    def send_logout(self):
        logout_data = LoginData(self._username)
        packet = Packet(logout_data, tag=PacketTag.LOGOUT)
        self.send(packet)
        print("You are logged out now.")

    def send_logout_now(self):
        if not self.socket:
            return
        logout_data = LoginData(self._username)
        pkt = Packet(logout_data, tag=PacketTag.LOGOUT)
        try:
            self.socket.sendall(pkt.encode())
            print("Logout sent now.")
        except OSError as e:
            print("Could not send logout:", e)


class GameController:
    def __init__(self, client_game_state: ClientGameState, dispatcher: UIEventDispatcher):
        self.dispatcher = dispatcher
        self.client_game_state = client_game_state
        self.login_service = LoginService(self)
        self._connection_service: Optional[ConnectionService] = None
        self.dispatcher.subscribe(Events.ATTACK_CLICKED, self.on_attack_clicked)
        self.dispatcher.subscribe(Events.LOGOUT_CLICKED, self.on_logout_clicked)

    def on_logged_in(self, login_reply: LoginReplyData):
        if self._connection_service:
            self._connection_service.stop()

        print(f"The server {login_reply.server_ip}:{login_reply.server_port} has been assigned to you.")

        # start new connection service
        connect_to_address = (login_reply.server_ip, login_reply.server_port)
        self._connection_service = ConnectionService(connect_to_address, login_reply.username, self.client_game_state, self.dispatcher)
        self._connection_service.start()

        # send login packet to the assigned server
        login_packet = Packet(LoginData(login_reply.username), tag=PacketTag.LOGIN)
        self._connection_service.send(login_packet)


    def on_attack_clicked(self):
        """Callback for when the attack button is clicked."""
        self.client_game_state.attack_boss()
        self._connection_service.send_attack()
        self.dispatcher.emit(Events.UPDATE_GAME_STATE, self.client_game_state, [])
        return self.client_game_state.boss

    def on_logout_clicked(self):
        if self._connection_service:
            self._connection_service.send_logout()
        self.client_game_state.player.logged_in = False
        self.dispatcher.emit(Events.LOGGED_OUT)

    def shutdown_on_close(self):
        if self._connection_service:
            try:
                self._connection_service.send_logout_now()
            finally:
                self._connection_service.stop()
                self._connection_service = None

        self.client_game_state.player.logged_in = False
        self.dispatcher.emit(Events.LOGGED_OUT)
