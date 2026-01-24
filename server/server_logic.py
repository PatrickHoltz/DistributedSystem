import multiprocessing as mp

import time
import uuid

from shared.data import *
from shared.sockets import Packet, PacketTag, BroadcastListener, BroadcastSocket, TCPServerConnection


class GameStateManager:
    """Manager of the overall game state."""
    base_damage = 10
    ONLINE_TTL_SECONDS = 10.0

    def __init__(self):
        self._game_state = GameStateData(players={}, boss=self._create_boss(1))
        self.latest_damage_numbers: list[int] = []

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
    def _create_boss(cls, stage: int) -> BossData:
        health = 100 + (stage - 1) * 20
        return BossData(name=f"Alien {stage}", stage=stage, health=health, max_health=health)

    def apply_attack(self, username: str):
        """Reads the damage and returns it. Does not mutate the boss."""
        if username in self._game_state.players:
            damage = self._game_state.players[username].damage
            self.latest_damage_numbers.append(damage)
            return damage
        return 0

    def _advance_boss_stage(self):
        new_stage = self._game_state.boss.stage + 1
        new_boss = self._create_boss(new_stage)
        self._game_state.boss = new_boss

    def set_boss_health(self, health: int ):
        """Sets the boss health. Only the leader should call this"""
        if health < 0:
            health = 0
        if health > self._game_state.boss.max_health:
            health = self._game_state.boss.max_health
        self._game_state.boss.health = health

    def advance_boss_stage(self):
        """Only leader should call"""
        self._advance_boss_stage()

    def set_boss(self, boss: BossData):
        """Follower takes boss from leader"""
        self._game_state.boss = boss

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
            boss=self._game_state.boss,
            player_count=self.get_online_player_count(),
            player=self._game_state.players[username],
            latest_damages=self.latest_damage_numbers
        )

    def get_boss(self) -> BossData:
        return self._game_state.boss
    
    def get_online_player_count(self) -> int:
        self._refresh_online_flags()
        online_players = [p for p in self._game_state.players.values() if p.online]
        return len(online_players)


class ConnectionManager:
    """Maintains active client connections and handles new logins using a broadcast listener."""
    CLIENT_HEARTBEAT_TIMEOUT = 6.0
    CLIENT_HEARTBEAT_INTERVAL = 2.0

    def __init__(self, server_loop: 'ServerLoop', uuid: int = -1):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.last_seen: dict[str, float] = {}

        self.login_listener = BroadcastListener(
            on_message=self.handle_login,
            uuid=uuid)
        self.login_listener.start()

        self.server_loop = server_loop
        self._next_client_ping = time.monotonic() + self.CLIENT_HEARTBEAT_INTERVAL

    def _add_connection(self, username: str, address: tuple[str, int]) -> int:
        """Adds a new active connection and registers it in the game state manager.
        Returns the port number the client communicator is listening on.
        """
        self.server_loop.game_state_manager.login_player(username)

        communicator = ClientCommunicator(("", 0), username, self.server_loop.in_queue)
        communicator.start()

        self.active_connections[username] = communicator
        self.last_seen[username] = time.monotonic()

        # Wait until the communicator is ready and retrieve its port
        server_port = communicator.get_port()

        print("Client connection established for", username, "on port", server_port)
        return server_port

    def remove_connection(self, username: str):
        """Closes an active connection."""

        if username in self.active_connections:
            self.active_connections[username].terminate()
            del self.active_connections[username]
        self.last_seen.pop(username, None)
        self.server_loop.game_state_manager.logout_player(username)

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
            self.server_loop.multicast_packet(Packet(StringMessage("ping"), tag=PacketTag.CLIENT_PING))

        to_drop = []
        for username in list(self.active_connections.keys()):
            last = self.last_seen.get(username, 0.0)
            if now - last > self.CLIENT_HEARTBEAT_TIMEOUT:
                to_drop.append(username)

        for username in to_drop:
            print(f"Heartbeat timeout: {username}")
            self.remove_connection(username)

    def handle_login(self, packet: Packet, address: tuple[str, int]):
        """Handles incoming login requests and establishes a new client communicator if the login is valid. Returns a response packet with the player's game state or None."""
        
        #leader messages
        if packet.tag in {
            PacketTag.SERVER_HELLO,
            PacketTag.BULLY_ELECTION,
            PacketTag.BULLY_OK,
            PacketTag.BULLY_COORDINATOR,
            PacketTag.BULLY_LEADER_HEARTBEAT,
        }:
            return self.server_loop.handle_leader_message(packet, address)

        if packet.tag in {PacketTag.GOSSIP_PLAYER_STATS, PacketTag.GOSSIP_BOSS_SYNC}:
            return self.server_loop.handle_gossip_message(packet, address)

        if packet.tag == PacketTag.LOGIN:
            try:
                login_data = LoginData(**packet.content)
                print(f"Login request received by {login_data.username}")

                server_port = self._add_connection(login_data.username, address)

                game_state_update = self.server_loop.game_state_manager.get_player_state(login_data.username)
                login_reply = LoginReplyData(server_port, game_state_update)

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

    def __init__(self, address: tuple[str, int], username: str, in_queue: mp.Queue):
        super().__init__(in_queue)

        self._username = username

    def _handle_packet(self, packet: Packet):
        """Checks packet validity and provides the server loop with it."""

        data_class = self.TAG_TO_DATA.get(packet.tag)

        # Abort if the packet tag is unknown
        if not data_class:
            print(f"Unknown packet tag {packet.tag} received. Aborting packet.")
            return

        typed_packet = self.get_typed_packet(packet, data_class)

        # Abort if the packet content could not be transformed into the appropriate data class
        if not typed_packet:
            print(f"Corrupt packet received. Aborting packet.")
            return

        print(f"Packet '{typed_packet.tag}' successfully received.")
        # Put the received, typed packet onto the shared in_queue so the
        # main ServerLoop process can consume it.
        self._recv_queue.put((self._username, typed_packet))
