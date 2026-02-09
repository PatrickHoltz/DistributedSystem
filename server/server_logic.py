from __future__ import annotations

import multiprocessing as mp
import socket
import time
from operator import attrgetter
from typing import TYPE_CHECKING
from typing import override
from uuid import UUID

from server.server_sockets import TCPListener, ServerUDPSocket
from shared.data import *
from shared.packet import PacketTag, Packet
from shared.sockets import BroadcastListener, TCPServerConnection, SocketUtils, Address
from shared.utils import Debug

if TYPE_CHECKING:
    from server_loop import ServerLoop   # only imported for type checkers

class GameStateManager:
    """Manager of the overall game state."""
    base_damage = 10
    ONLINE_TTL_SECONDS = 10.0

    def __init__(self):
        self._game_state = GameStateData(players={}, monster=self._create_monster(1))
        self.latest_damage_numbers: list[int] = []
        self.overall_dmg = 0

    def touch_player(self, username: str):
        """Update last_seen_ts for TTL-online"""
        now = time.time()
        player = self._game_state.players.get(username)
        if player is None:
            return
        player.last_seen_ts = now
        player.online = True

    def _refresh_online_flags(self):
        """Derive online from last_seen_ts using TTL."""
        now = time.time()
        for player in self._game_state.players.values():
            last = float(getattr(player, "last_seen_ts", 0.0))
            player.online = (now - last) < self.ONLINE_TTL_SECONDS

    def get_player_stats(self) -> PlayerStats:
        """Gossip-safe stats: username/level/last_seen_ts per player."""
        self._refresh_online_flags()
        players_meta: dict[str, dict] = {}
        for username, p in self._game_state.players.items():
            players_meta[username] = {
                "username": p.username,
                "level": int(p.level),
                "last_seen_ts": float(getattr(p, "last_seen_ts", 0.0)),
            }
        return PlayerStats(
            player_count=self.get_online_player_count(),
            players=players_meta
        )

    def merge_player_stats(self, incoming_players: dict[str, dict]):
        """Merge gossip-safe player metaData into local state."""
        for username, metaData in incoming_players.items():
            incoming_username = metaData.get("username", username)
            incoming_level = int(metaData.get("level", 1))
            incoming_seen = float(metaData.get("last_seen_ts", 0.0))

            cur = self._game_state.players.get(username)
            if cur is None:
                self._game_state.players[username] = PlayerData(
                    username=incoming_username,
                    damage=self.base_damage,
                    online=True,
                    level=incoming_level,
                    last_seen_ts=incoming_seen,
                )
                continue

            cur.level = max(int(cur.level), incoming_level)
            cur.last_seen_ts = max(float(getattr(cur, "last_seen_ts", 0.0)), incoming_seen)

        self._refresh_online_flags()


    @classmethod
    def _create_monster(cls, stage: int) -> MonsterData:
        health = 100 + (stage - 1) * 20
        return MonsterData(name=f"Alien {stage}", stage=stage, health=health, max_health=health)

    def apply_attack(self, username: str):
        """Applies an attack from the given player to the current monster. Returns the damage dealt to the monster.
        """
        if username not in self._game_state.players:
            return 0
        
        damage = self._game_state.players[username].damage
        self._game_state.monster.health -= damage

        self.latest_damage_numbers.append(damage)
        self.overall_dmg += damage
        return damage
        

    def apply_attacks(self, damage: int) -> None:
        """Set the health of the monster to the max healt minus the damage. 
        Damage should be the sum og all damage from all servers."""
        self._game_state.monster.health = self._game_state.monster.max_health - damage

    def set_monster_health(self, health: int ):
        """Sets the monster health. Only the leader should call this"""
        if health < 0:
            health = 0
        if health > self._game_state.monster.max_health:
            health = self._game_state.monster.max_health
        self._game_state.monster.health = health

    def get_next_monster(self):
        """Only leader should call"""
        next_stage = self._game_state.monster.stage + 1
        return self._create_monster(next_stage)

    def set_monster(self, monster: MonsterData):
        """Follower takes monster from leader"""
        self._game_state.monster = monster
        self.overall_dmg = 0

    def login_player(self, username: str):
        """Creates a new player entry if it does not exist and marks the player as online."""
        now = time.time()
        if username not in self._game_state.players:
            self._game_state.players[username] = PlayerData(
                username=username,
                damage=self.base_damage,
                online=True,
                level=1,
                last_seen_ts=now,
            )
        else:
            player = self._game_state.players[username]
            player.online = True
            player.last_seen_ts = now
    
    def logout_player(self, username: str):
        if username in self._game_state.players:
            self._game_state.players[username].online = False
            self._game_state.players[username].last_seen_ts = 0.0

    def get_player_state(self, username: str) -> PlayerGameStateData:
        self._refresh_online_flags()
        return PlayerGameStateData(
            monster=self._game_state.monster,
            player_count=self.get_online_player_count(),
            player=self._game_state.players[username],
            latest_damages=self.latest_damage_numbers
        )

    def get_monster(self) -> MonsterData:
        return self._game_state.monster

    def get_online_player_count(self) -> int:
        self._refresh_online_flags()
        online_players = [p for p in self._game_state.players.values() if p.online]
        return len(online_players)

    def is_player_online(self, username: str):
        if username in self._game_state.players:
            return self._game_state.players[username].online
        return False


class ConnectionManager:
    """Maintains active client connections and handles new logins using a broadcast listener."""
    CLIENT_HEARTBEAT_TIMEOUT = 6.0
    CLIENT_HEARTBEAT_INTERVAL = 2.0

    def __init__(self, server_loop: ServerLoop, server_uuid: str):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.last_seen: dict[str, float] = {}
        self._next_client_ping = time.monotonic() + self.CLIENT_HEARTBEAT_INTERVAL

        self.server_view: dict[str, ServerState] = {}

        self.server_loop: ServerLoop = server_loop

        # handles incoming broadcasts
        self.broadcast_listener = BroadcastListener(
            on_message=self.handle_broadcast,
            server_uuid=UUID(server_uuid).int,
            stop_event=server_loop.stop_event,
        )

        # handles regular login requests to the assigned server
        self.client_listener = TCPListener(self, server_loop.stop_event)

        # handles incoming udp unicast packets and sends unicast / broadcast
        self.udp_socket = ServerUDPSocket(self, server_loop.stop_event)

        # wait until the listeners obtained the listener addresses
        self.udp_listen_port = self.udp_socket.get_port()
        self.tcp_listener_port = self.client_listener.get_port()

        self.server_info = ServerInfo(server_uuid, 0, SocketUtils.local_ip, self.udp_listen_port, self.tcp_listener_port)
        Debug.log(f"IP: {self.server_info.ip}, UDP port: {self.server_info.udp_port}, TCP port: {self.server_info.tcp_port}")
        
        self.broadcast_listener.start()
        self.client_listener.start()
        self.udp_socket.start()

    def _assign_client(self) -> ServerInfo:
        """Returns the server info of the server which is occupied least"""
        least_used_other_server = min(self.server_view.values(), key=attrgetter('server_info.occupancy'), default=ServerState(self.server_info, 0)).server_info

        return min(least_used_other_server, self.server_info, key=attrgetter('occupancy'))

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

        self.server_info.occupancy -= 1

    def mark_seen(self, username: str):
        self.last_seen[username] = time.monotonic()
        self.server_loop.game_state_manager.touch_player(username)

    def tick_client_heartbeat(self, now: float):
        """
        - Sends in an interval ping message to all connected Clients
        - Checks when the client was last seen (self.last_seen[username]) and removes connection if client didn't answer in CLIENT_HEARTBEAT_TIMEOUT time
        """
        if now >= self._next_client_ping:
            self._next_client_ping += self.CLIENT_HEARTBEAT_INTERVAL
            self.server_loop.deliver_packet_to_clients(Packet(StringMessage("ping"), tag=PacketTag.CLIENT_PING))

        to_drop = []
        for username in list(self.active_connections.keys()):
            last = self.last_seen.get(username, 0.0)
            if now - last > self.CLIENT_HEARTBEAT_TIMEOUT:
                to_drop.append(username)

        for username in to_drop:
            print(f"Heartbeat timeout: {username}")
            self.remove_connection(username)

    def handle_broadcast(self, packet: Packet, address: Address) -> None:
        """Handles incoming login requests and establishes a new client communicator if the login is valid. Returns a response packet with the player's game state or None."""
        match packet.tag:
            case PacketTag.GOSSIP_PLAYER_STATS | PacketTag.GOSSIP_MONSTER_SYNC:
                self.server_loop.handle_gossip_message(packet)
            case PacketTag.LOGIN:
                self._handle_login(packet, address)
            case PacketTag.BULLY_ELECTION | PacketTag.BULLY_COORDINATOR | PacketTag.BULLY_LEADER_HEARTBEAT:
                self.server_loop.handle_bully_message(packet, address)
        return

    def _handle_login(self, packet: Packet, address: Address) -> None:
        # Non leaders ignore login messages
        if not self.server_loop.is_leader:
            return

        try:
            login_data = LoginData(**packet.content)
            Debug.log(f"Login request received by {login_data.username}", "LEADER")

            # check if player is already logged in
            if self.server_loop.game_state_manager.is_player_online(login_data.username):
                login_reply = LoginReplyData("NONE", 0, login_data.username)
            else:
                server_info = self._assign_client()
                login_reply = LoginReplyData(server_info.ip, server_info.tcp_port, login_data.username)
            response = Packet(login_reply, tag=PacketTag.LOGIN_REPLY)
            self.udp_socket.send_to(response, address)
        except TypeError as e:
            print("Invalid login data received.", e)

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
