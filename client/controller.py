from __future__ import annotations

from client.events import UIEventDispatcher, Events
from shared.data import *
from shared.sockets import Packet, PacketTag, BroadcastSocket, TCPConnection
import multiprocessing as mp
from model import ClientGameState
import events


class LoginService:
    '''Service to send login requests to the server'''

    def __init__(self, game_controller: 'GameController'):
        self._game_controller = game_controller
        self._game_controller.dispatcher.subscribe(Events.LOGIN_CLICKED, self.login)

    def login(self, username: str):
        '''Sends a login broadcast and waits for a response to update the game state.'''

        print("Logging in...")
        login_data = LoginData(username)
        packet = Packet(login_data, tag=PacketTag.LOGIN)
        login_broadcast = BroadcastSocket(packet, self._handle_login_response, self._handle_login_timeout)
        login_broadcast.start()
        login_broadcast.join()

    def _handle_login_response(self, packet: Packet, address: tuple[str, int]):
        '''Called when a login response is received. Updates the game state and starts a TCP connection with the responder.'''

        if packet.tag == PacketTag.PLAYER_GAME_STATE:
            try:
                game_state = PlayerGameStateData.from_dict(packet.content)
                self._game_controller.on_logged_in(game_state.player.username, address, game_state)
            except TypeError as e:
                print("Invalid game state received.", e)
    
    def _handle_login_timeout(self):
        print("Login failed. Server did not respond to login request.")


class ConnectionService(TCPConnection):

    def __init__(self, address: tuple[str, int], game_state_manager: ClientGameState):
        super().__init__(address)
        self._game_state_manager = game_state_manager
        self._username: str = None
        self.stop_event = mp.Event()

    def run(self):
        while not self.stop_event.is_set():
            # Wait for incoming packet
            packet = self.get_packet(self._recv_timeout)
            if packet:
                self._handle_packet(packet)
    
    
    def _handle_packet(self, packet: Packet):
        try:
            match packet.tag:
                case PacketTag.CLIENT_PING:
                    pong = Packet(StringMessage("pong"), tag=PacketTag.CLIENT_PONG)
                    self.send(pong)
                    return

                case PacketTag.CLIENT_PONG:
                    return

                case PacketTag.PLAYER_GAME_STATE:
                    typed_packet: Packet = self._get_typed_packet(packet, PlayerGameStateData)
                    self._game_state_manager.update(typed_packet.content)
                    events.UPDATE_GAME_STATE.trigger(typed_packet.content)

                case PacketTag.NEW_BOSS:
                    typed_packet = self._get_typed_packet(packet, BossData)
                    self._game_state_manager.boss.update_state(typed_packet)
                    events.UPDATE_GAME_STATE.trigger(typed_packet.content)

                case PacketTag.BOSS_DEAD:
                    typed_packet = self._get_typed_packet(packet, StringMessage)
                    self._game_state_manager.boss.set_dead()
                    events.UPDATE_GAME_STATE.trigger(typed_packet.content)
                case _:
                    raise ValueError(f"Unknown packet tag received: {packet.tag}")
            
        except Exception as e:
            print("Error handling packet:", e)

    def send_attack(self, damage: int):
        '''Sends an attack packet to the server with the specified damage.'''
        if self._tcp_connection and self._username:
            attack_data = AttackData(username=self._username, damage=damage)
            packet = Packet(attack_data, tag=PacketTag.ATTACK)
            self.send(packet)

    def send_logout(self):
        '''Sends a logout packet to the server.'''
        if self._username:
            logout_data = LoginData(self._username)
            packet = Packet(logout_data, tag=PacketTag.LOGOUT)
            self.send(packet)
            self.terminate()
            print("You are logged out now.")

class GameController:
    def __init__(self, client_game_state: ClientGameState, dispatcher: UIEventDispatcher):
        self.dispatcher = dispatcher
        self.client_game_state = client_game_state
        self.login_service = LoginService(self)
        self._connection_service: ConnectionService = None
        self.dispatcher.subscribe(Events.ATTACK_CLICKED, self.on_attack_clicked)

    def on_logged_in(self, username: str, address: tuple[str, int], game_state: PlayerGameStateData):
        if self._connection_service:
            self._connection_service.stop_event.set()
        print(f"You are now logged in as {username}")
        self._connection_service = ConnectionService(address, self.client_game_state)
        #self._connection_service.start()
        self.client_game_state.update(game_state)
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