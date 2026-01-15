import uuid
from shared.sockets import TCPConnection, Packet, PacketTag, BroadcastListener, BroadcastSocket
from shared.data import *
import multiprocessing as mp
from typing import TypeVar
from threading import Timer
import time


class GameStateManager:
    """Manager of the overall game state."""
    base_damage = 10

    def __init__(self, server_loop: 'ServerLoop'):
        self._game_state = GameState(players={}, boss=self._create_boss(1))
        self._server_loop = server_loop

    def _create_boss(self, stage: int) -> BossData:
        health = stage * 100
        return BossData(name=f"Alien{stage}", stage=stage, health=health, max_health=health)

    def apply_attack(self, username: str, damage: int):
        if username in self._game_state.players:
            self._game_state.boss.health -= damage
            if self._game_state.boss.health < 0:
                self._game_state.boss.health = 0
                self._server_loop.multicast_packet(Packet(StringMessage("Boss dead"), tag=PacketTag.BOSS_DEAD))
                Timer(3.0, self._advance_boss_stage).start()
    
    def _advance_boss_stage(self):
        new_stage = self._game_state.boss.stage + 1
        new_boss = self._create_boss(new_stage)
        self._game_state.boss = new_boss
        self._server_loop.multicast_packet(Packet(new_boss, tag=PacketTag.NEW_BOSS))

    def login_player(self, username: str):
        """Creates a new player entry if it does not exist and marks the player as online in all cases."""
        if username not in self._game_state.players:
            self._game_state.players[username] = PlayerData(username=username, damage=self.base_damage, level=1, online=True)
        else:
            self._game_state.players[username].online = True
    
    def logout_player(self, username: str):
        if username in self._game_state.players:
            self._game_state.players[username].online = False

    def get_state_update(self, username: str) -> PlayerGameState:
        return PlayerGameState(
            boss=self._game_state.boss,
            player_count=self.get_online_player_count(),
            player=self._game_state.players[username]
        )
    
    def get_online_player_count(self) -> int:
        online_players = [p for p in self._game_state.players.values() if p.online]
        return len(online_players)


class ConnectionManager:
    """Maintains active client connections and handles new logins using a broadcast listener."""
    CLIENT_HEARTBEAT_TIMEOUT = 6.0
    CLIENT_HEARTBEAT_INTERVAL = 2.0

    def __init__(self, server_loop: 'ServerLoop'):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.last_seen: dict[str, float] = {}

        self.login_listener = BroadcastListener(
            on_message=self.handle_login)
        self.login_listener.start()

        self.server_loop = server_loop
        self._next_client_ping = time.monotonic() + self.CLIENT_HEARTBEAT_INTERVAL

    def _add_connection(self, username: str, address: tuple[str, int]):
        """Adds a new active connection and registers it in the game state manager."""

        self.server_loop.game_state_manager.login_player(username)

        communicator = ClientCommunicator(address, username, self.server_loop.in_queue)
        self.active_connections[username] = communicator
        self.last_seen[username] = time.monotonic()
        communicator.start()

    def remove_connection(self, username: str):
        """Closes an active connection."""

        if username in self.active_connections:
            self.active_connections[username].terminate()
            del self.active_connections[username]
        self.last_seen.pop(username, None)
        self.server_loop.game_state_manager.logout_player(username)

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

    def handle_login(self, packet: Packet, address: tuple[str, int]):
        """Handles incoming login requests and establishes a new client communicator if the login is valid. Returns a response packet with the player's game state or None."""

        if packet.tag == PacketTag.LOGIN:
            try:
                login_data = LoginData(**packet.content)
                print(f"Login registered: {login_data.username}")

                
                self._add_connection(login_data.username, address)
                game_state_update = self.server_loop.game_state_manager.get_state_update(login_data.username)

                response = Packet(game_state_update, tag=PacketTag.PLAYER_GAME_STATE)
                return response
            except TypeError as e:
                print("Invalid login data received.", e)
        return None


class ServerLoop:
    """Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick."""
    MAX_MESSAGES_PER_TICK = 50

    def __init__(self):
        super().__init__()
        self.server_uuid = str(uuid.uuid4())

        self.connection_manager = ConnectionManager(self)
        self.game_state_manager = GameStateManager(self)

        self._is_stopped = False
        self.in_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.out_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.tick_rate = 0.1  # ticks per second

        self.run()

    def run(self):
        hello_packet = Packet(ServerHello(uuid=self.server_uuid), tag=PacketTag.SERVER_HELLO)

        # sends 3 times because UDP can drop packages
        broadcast_socket = None
        for _ in range(3):
            broadcast_socket = BroadcastSocket(
                hello_packet,
                response_handler=self.handle_leader_message,
                broadcast_port=10002,
                timeout_s= 1.0)
            broadcast_socket.start()
            time.sleep(0.2)

        reply = broadcast_socket.future.result(timeout=2.0)
        if reply is None:
            print("No leader. My UUID", self.server_uuid)
        else:
            print("Answer received:", reply.tag, reply.content)

        while not self._is_stopped:
            now = time.monotonic()

            self._process_incoming_messages()
            self._update_game_states()
            self._send_outgoing_messages()

            #self.connection_manager.tick_client_heartbeat(now)

            time.sleep(self.tick_rate)

    def stop(self):
        self._is_stopped = True
    
    def multicast_packet(self, packet: Packet):
        """Writes a given packet into the outgoing queue for all connected clients."""
        for username in self.connection_manager.active_connections.keys():
            self.out_queue.put((username, packet))

    def _update_game_states(self):
        """Writes game state updates for all connected clients into the outgoing queue."""
        for username in self.connection_manager.active_connections.keys():
            game_state_update = self.game_state_manager.get_state_update(username)
            self.out_queue.put((username, Packet(game_state_update, tag=PacketTag.PLAYER_GAME_STATE)))

    def _process_incoming_messages(self):
        processed = 0
        while not self.in_queue.empty() and processed < self.MAX_MESSAGES_PER_TICK:
            username, packet = self.in_queue.get()
            processed += 1

            self.connection_manager.mark_seen(username)

            match packet.tag:
                case PacketTag.CLIENT_PONG:
                    pass

                case PacketTag.CLIENT_PING:
                    self.out_queue.put((username, Packet(StringMessage("pong"), tag=PacketTag.CLIENT_PONG)))

                case PacketTag.ATTACK:
                    self.game_state_manager.apply_attack(username, packet.content['damage'])

                case PacketTag.LOGOUT:
                    self.connection_manager.remove_connection(username)
                    self.game_state_manager.logout_player(username)
                case _:
                    pass
            # response = None
            # self.out_queue.put((username, response))

    def _send_outgoing_messages(self):
        processed = 0
        while not self.out_queue.empty() and processed < self.MAX_MESSAGES_PER_TICK:
            username, packet = self.out_queue.get()
            if username in self.connection_manager.active_connections:
                communicator = self.connection_manager.active_connections[username]
                communicator.send(packet)
            processed += 1

    def handle_leader_message(self, packet: Packet, address: tuple[str, int]):
        """TODO"""
        pass
    


class ClientCommunicator(TCPConnection):
    """ Communicates with a client using TCP connection. Incoming packets are filtered and their content is transformed into the appropriate data classes.
        Outgoing packets can be sent using the send method.
    """

    T = TypeVar('T')

    TAG_TO_DATA = {
        PacketTag.ATTACK: AttackData,
        PacketTag.LOGOUT: LoginData,
        PacketTag.CLIENT_PONG: StringMessage,
        PacketTag.CLIENT_PING: StringMessage,
    }

    def __init__(self, address: tuple[str, int], username: str, in_queue: mp.Queue):
        """ClientCommunicator runs in its own process. Only pass pickable
        objects into __init__ (primitives and multiprocessing queues)."""
        super().__init__(address)
        self._username = username
        self._in_queue = in_queue
        self._stop_event = mp.Event()
        self._recv_timeout = 2.0

    def run(self):
        while not self._stop_event.is_set():
            # Wait for incoming packet
            packet = self.get_packet(self._recv_timeout)
            if packet:
                self._handle_packet(packet)

    def _handle_packet(self, packet: Packet):
        """Checks packet validity and provides the server loop with it."""

        data_class = self.TAG_TO_DATA.get(packet.tag)
        if not data_class:
            raise ValueError(f"Unknown packet tag received: {packet.tag}")

        typed_packet = self._get_typed_packet(packet, data_class)

        # Put the received, typed packet onto the shared in_queue so the
        # main ServerLoop process can consume it.
        self._in_queue.put((self._username, typed_packet))

    def _get_typed_packet(self, packet: Packet, content_type: T) -> Packet:
        # Validate packet content
        try:
            content = content_type(**packet.content)
            return Packet(content=content, tag=packet.tag)
        except TypeError:
            raise TypeError(
                f"Could not extract packet content of type {content_type}")