import uuid
from shared.sockets import _TCPConnection, Packet, PacketTag, BroadcastListener, BroadcastSocket, TCPServerConnection
from shared.data import *
import multiprocessing as mp
from typing import TypeVar
from threading import Timer, Thread
import time


class GameStateManager:
    """Manager of the overall game state."""
    base_damage = 10

    def __init__(self):
        self._game_state = GameStateData(players={}, boss=self._create_boss(1))

    @classmethod
    def _create_boss(cls, stage: int) -> BossData:
        health = stage * 100
        return BossData(name=f"Alien{stage}", stage=stage, health=health, max_health=health)

    def apply_attack(self, username: str):
        """Applies an attack from the given player to the current boss. Advances the boss stage if the boss is defeated.
        Returns True if the boss is defeated afterward, False otherwise.
        """
        if username in self._game_state.players:
            damage = self._game_state.players[username].damage
            self._game_state.boss.health -= damage
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
            self._game_state.players[username] = PlayerData(username=username, damage=self.base_damage, level=1, online=True)
        else:
            self._game_state.players[username].online = True
    
    def logout_player(self, username: str):
        if username in self._game_state.players:
            self._game_state.players[username].online = False

    def get_player_state(self, username: str) -> PlayerGameStateData:
        return PlayerGameStateData(
            boss=self._game_state.boss,
            player_count=self.get_online_player_count(),
            player=self._game_state.players[username]
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

    def __init__(self, server_loop: 'ServerLoop'):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.last_seen: dict[str, float] = {}

        self.login_listener = BroadcastListener(
            on_message=self.handle_login)
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
                print(f"Login request received by {login_data.username}")

                server_port = self._add_connection(login_data.username, address)


                game_state_update = self.server_loop.game_state_manager.get_player_state(login_data.username)
                login_reply = LoginReplyData(server_port, game_state_update)

                response = Packet(login_reply, tag=PacketTag.LOGIN_REPLY)

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
        self.game_state_manager = GameStateManager()

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
            print("———————————NO LEADER?———————————\n⠀⣞⢽⢪⢣⢣⢣⢫⡺⡵⣝⡮⣗⢷⢽⢽⢽⣮⡷⡽⣜⣜⢮⢺⣜⢷⢽⢝⡽⣝\n⠸⡸⠜⠕⠕⠁⢁⢇⢏⢽⢺⣪⡳⡝⣎⣏⢯⢞⡿⣟⣷⣳⢯⡷⣽⢽⢯⣳⣫⠇\n⠀⠀⢀⢀⢄⢬⢪⡪⡎⣆⡈⠚⠜⠕⠇⠗⠝⢕⢯⢫⣞⣯⣿⣻⡽⣏⢗⣗⠏⠀\n⠀⠪⡪⡪⣪⢪⢺⢸⢢⢓⢆⢤⢀⠀⠀⠀⠀⠈⢊⢞⡾⣿⡯⣏⢮⠷⠁⠀⠀\n⠀⠀⠀⠈⠊⠆⡃⠕⢕⢇⢇⢇⢇⢇⢏⢎⢎⢆⢄⠀⢑⣽⣿⢝⠲⠉⠀⠀⠀⠀\n⠀⠀⠀⠀⠀⡿⠂⠠⠀⡇⢇⠕⢈⣀⠀⠁⠡⠣⡣⡫⣂⣿⠯⢪⠰⠂⠀⠀⠀⠀\n⠀⠀⠀⠀⡦⡙⡂⢀⢤⢣⠣⡈⣾⡃⠠⠄⠀⡄⢱⣌⣶⢏⢊⠂⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⠀⢝⡲⣜⡮⡏⢎⢌⢂⠙⠢⠐⢀⢘⢵⣽⣿⡿⠁⠁⠀⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⠀⠨⣺⡺⡕⡕⡱⡑⡆⡕⡅⡕⡜⡼⢽⡻⠏⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⠀⣼⣳⣫⣾⣵⣗⡵⡱⡡⢣⢑⢕⢜⢕⡝⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⣴⣿⣾⣿⣿⣿⡿⡽⡑⢌⠪⡢⡣⣣⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⡟⡾⣿⢿⢿⢵⣽⣾⣼⣘⢸⢸⣞⡟⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\n⠀⠀⠀⠀⠁⠇⠡⠩⡫⢿⣝⡻⡮⣒⢽⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\n—————————————————————————————\n")
            print("My UUID", self.server_uuid)
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
            game_state_update = self.game_state_manager.get_player_state(username)
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
                    boss_defeated = self.game_state_manager.apply_attack(username)
                    if boss_defeated:
                        new_boss = self.game_state_manager.get_boss()
                        self.multicast_packet(Packet(new_boss, tag=PacketTag.NEW_BOSS))

                case PacketTag.LOGOUT:
                    self.connection_manager.remove_connection(username)
                    self.game_state_manager.logout_player(username)
                    print(f"Player '{username}' logged out.")
                case _:
                    pass

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

        typed_packet = self.get_typed_packet(packet, data_class)

        # Abort if the packet content could not be transformed into the appropriate data class
        if not typed_packet:
            print(f"Corrupt packet received. Aborting packet.")
            return

        print(f"Packet '{typed_packet.tag}' successfully received.")
        # Put the received, typed packet onto the shared in_queue so the
        # main ServerLoop process can consume it.
        self._recv_queue.put((self._username, typed_packet))
