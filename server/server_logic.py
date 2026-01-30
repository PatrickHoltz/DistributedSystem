import multiprocessing as mp
import socket
import time
from operator import attrgetter
from threading import Thread
from typing import override
from uuid import UUID

from shared.data import *
from shared.packet import PacketTag, Packet
from shared.sockets import BroadcastListener, TCPServerConnection, _TCPConnection, \
    SocketUtils
from shared.utils import Debug


class GameStateManager:
    """Manager of the overall game state."""
    base_damage = 10

    def __init__(self):
        self._game_state = GameStateData(players={}, boss=self._create_boss(1))
        self.latest_damage_numbers: list[int] = []

    @classmethod
    def _create_boss(cls, stage: int) -> BossData:
        health = 100 + (stage - 1) * 20
        return BossData(name=f"Alien {stage}", stage=stage, health=health, max_health=health)

    def apply_attack(self, username: str):
        """Applies an attack from the given player to the current boss. Advances the boss stage if the boss is defeated.
        Returns True if the boss is defeated afterward, False otherwise.
        """
        if username in self._game_state.players:
            damage = self._game_state.players[username].damage
            self._game_state.boss.health -= damage
            self.latest_damage_numbers.append(damage)
            boss_defeated = self._game_state.boss.health <= 0
            if boss_defeated:
                print("Boss defeated. Advancing to next stage.")
                self._game_state.boss.health = 0
                self._advance_boss_stage()
            return boss_defeated
        return self._game_state.boss.health <= 0

    def _advance_boss_stage(self):
        new_stage = self._game_state.boss.stage + 1
        new_boss = self._create_boss(new_stage)
        self._game_state.boss = new_boss

    def login_player(self, username: str):
        """Creates a new player entry if it does not exist and marks the player as online in all cases."""
        if username not in self._game_state.players:
            self._game_state.players[username] = PlayerData(username=username, damage=self.base_damage, level=1,
                                                            online=True)
        else:
            self._game_state.players[username].online = True

    def logout_player(self, username: str):
        if username in self._game_state.players:
            self._game_state.players[username].online = False

    def get_player_state(self, username: str) -> PlayerGameStateData:
        return PlayerGameStateData(
            boss=self._game_state.boss,
            player_count=self.get_online_player_count(),
            player=self._game_state.players[username],
            latest_damages=self.latest_damage_numbers
        )

    def get_boss(self) -> BossData:
        return self._game_state.boss

    def get_online_player_count(self) -> int:
        online_players = [p for p in self._game_state.players.values() if p.online]
        return len(online_players)


class ConnectionManager:
    """Maintains active client connections and handles new logins using a broadcast listener."""
    CLIENT_HEARTBEAT_TIMEOUT = 6.0
    CLIENT_HEARTBEAT_INTERVAL = 2.0

    def __init__(self, server_loop: 'ServerLoop', server_uuid: str):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.last_seen: dict[str, float] = {}

        self.server_loop: 'ServerLoop' = server_loop

        # handles login requests to the leader
        self.broadcast_listener = BroadcastListener(
            on_message=self.handle_broadcast,
            server_uuid=UUID(server_uuid).int)
        self.broadcast_listener.start()

        # handles regular login requests to the assigned server
        self.client_listener = TCPListener(self)
        self.client_listener.start()

        # wait until client listener started to obtain the listener address
        self.listener_address = self.client_listener.get_address()
        self.server_info = ServerInfo(server_uuid, 0, self.listener_address[0], self.listener_address[1])

        self._next_client_ping = time.monotonic() + self.CLIENT_HEARTBEAT_INTERVAL

        self.server_view: dict[str, ServerInfo] = {
            self.server_loop.server_uuid: self.server_info
        }

    def _assign_client(self) -> ServerInfo:
        """Returns the server info of the server which is occupied least"""
        least_used_server = min(self.server_view.values(), key=attrgetter('occupancy'))
        return least_used_server

    def add_connection(self, username: str, conn_socket: socket.socket) -> int:
        """Adds a new active connection and registers it in the game state manager.
        Returns the port number the client communicator is listening on.
        """

        self.server_loop.game_state_manager.login_player(username)

        communicator = ClientCommunicator(conn_socket, username, self.server_loop.in_queue)
        communicator.start()

        # send login confirm
        player_state = self.server_loop.game_state_manager.get_player_state(username)
        login_confirm = Packet(player_state, tag=PacketTag.LOGIN_CONFIRM)
        communicator.send(login_confirm)

        self.active_connections[username] = communicator
        self.last_seen[username] = time.monotonic()

        # Wait until the communicator is ready and retrieve its port
        server_port = communicator.get_port()

        self.server_info.occupancy += 1

        Debug.log(f"Client connection established for {username}.", "SERVER")
        return server_port

    def remove_connection(self, username: str):
        """Closes an active connection."""

        if username in self.active_connections:
            self.active_connections[username].stop()
            del self.active_connections[username]
        self.last_seen.pop(username, None)
        self.server_loop.game_state_manager.logout_player(username)

        self.server_info.occupancy += 1

    def mark_seen(self, username: str):
        self.last_seen[username] = time.monotonic()

    def tick_client_heartbeat(self, now: float):
        """
        - Sends in an interval ping message to all connected Clients
        - Checks when the client was last seen (self.last_seen[username]) and removes connection if client didn't answer in CLIENT_HEARTBEAT_TIMEOUT time
        """
        if now >= self._next_client_ping:
            self._next_client_ping += self.CLIENT_HEARTBEAT_INTERVAL
            self.server_loop.multicast_packet(Packet(StringMessage("ping"), tag=PacketTag.CLIENT_PING))

        to_drop = []
        for username in list(self.active_connections.keys()):
            last = self.last_seen.get(username, 0.0)
            if now - last > self.CLIENT_HEARTBEAT_TIMEOUT:
                to_drop.append(username)

        for username in to_drop:
            print(f"Heartbeat timeout: {username}")
            self.remove_connection(username)

    def handle_broadcast(self, packet: Packet, _address: tuple[str, int]):
        """Handles incoming login requests and establishes a new client communicator if the login is valid. Returns a response packet with the player's game state or None."""

        if packet.tag == PacketTag.LOGIN:
            # Non leaders ignore login messages
            if not self.server_loop.is_leader:
                return None

            try:
                login_data = LoginData(**packet.content)
                Debug.log(f"Login request received by {login_data.username}", "LEADER")

                server_info = self._assign_client()
                login_reply = LoginReplyData(server_info.ip, server_info.listening_port, login_data.username)
                response = Packet(login_reply, tag=PacketTag.LOGIN_REPLY)

                return response
            except TypeError as e:
                print("Invalid login data received.", e)
        return None


class ClientCommunicator(TCPServerConnection):
    """ Communicates with a client using TCP connection. Incoming packets are filtered and their content is transformed into the appropriate data classes.
        Outgoing packets can be sent using the inherited send method.
    """

    TAG_TO_DATA = {
        PacketTag.ATTACK: AttackData,
        PacketTag.LOGOUT: LoginData,
        PacketTag.CLIENT_PONG: StringMessage,
        PacketTag.CLIENT_PING: StringMessage,
    }

    def __init__(self, conn_socket: socket.socket, username: str, in_queue: mp.Queue):
        super().__init__(conn_socket, in_queue)

        self._username = username

    @override
    def _handle_packet(self, packet: Packet):
        """Checks packet validity and provides the server loop with it."""

        data_class = self.TAG_TO_DATA.get(packet.tag)

        # Abort if the packet tag is unknown
        if not data_class:
            Debug.log(f"Unknown packet tag {packet.tag} received. Aborting packet.", "SERVER")
            return

        typed_packet = SocketUtils.get_typed_packet(packet, data_class)

        # Abort if the packet content could not be transformed into the appropriate data class
        if not typed_packet:
            Debug.log(f"Corrupt packet received. Aborting packet.", "SERVER")
            return

        Debug.log(f"Packet '{typed_packet.tag}' successfully received.", "SERVER")
        # Put the received, typed packet onto the shared in_queue so the
        # main ServerLoop process can consume it.
        self._recv_queue.put((self._username, typed_packet))


class TCPListener(_TCPConnection, Thread):
    """A socket which accepts incoming tcp connections if they deliver a login packet with them."""

    def __init__(self, connection_manager: ConnectionManager):
        super().__init__(mp.Queue())
        self._connection_manager = connection_manager

        # Queue to communicate the actual port back to the parent process
        self.addr_queue = mp.Queue()

    def run(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("0.0.0.0", 0))

        # set a high backlog so that no login request gets lost
        self.socket.listen(50)

        local_ip = SocketUtils.get_local_ip()
        self.addr_queue.put((local_ip, self.socket.getsockname()[1]))

        while True:
            client_sock, client_addr = self.socket.accept()

            packet = SocketUtils.recv_packet(client_sock)

            if packet.tag == PacketTag.LOGIN:
                login_data = LoginData(**packet.content)
                self._connection_manager.add_connection(login_data.username, client_sock)

            # Also read server heartbeats
            if packet.tag == PacketTag.SERVER_HEARTBEAT and self._connection_manager.server_loop.is_leader:
                server_info = ServerInfo(**packet.content)

                Debug.log("Server heartbeat received.", "LEADER")
                self._connection_manager.server_view[server_info.server_uuid] = server_info


    def get_address(self) -> tuple[str, int]:
        """Returns the port the server is listening on. Blocks until the port is available."""
        return self.addr_queue.get()
