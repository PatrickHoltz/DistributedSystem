import uuid as _uuid
from shared.sockets import Packet, PacketTag, BroadcastListener, BroadcastSocket, TCPServerConnection
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
        
        #leader messages
        if packet._tag in {
            PacketTag.SERVER_HELLO,
            PacketTag.ELECTION,
            PacketTag.OK,
            PacketTag.COORDINATOR,
            PacketTag.LEADER_HEARTBEAT,
        }:
            return self.server_loop.handle_leader_message(packet, address)

        if packet._tag == PacketTag.LOGIN:
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

        self.server_uuid = str(_uuid.uuid4())

        self.connection_manager = ConnectionManager(self)
        self.game_state_manager = GameStateManager()

        self._is_stopped = False
        self.in_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.out_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.tick_rate = 0.1  # ticks per second

        self.leader_uuid: str | None = None
        self.is_leader: bool = False
        self.election_in_progress: bool = False

        self.HEARTBEAT_INTERVAL = 2.0
        self.HEARTBEAT_TIMEOUT = 6.0
        self.ELECTION_OK_WAIT = 1.0
        self.COORDINATOR_WAIT = 3.0

        self._next_hb = time.monotonic() + self.HEARTBEAT_INTERVAL
        self._last_leader_seen = time.monotonic()
        self.run()

    def run(self):
        hello_packet = Packet(ServerHello(uuid=self.server_uuid), tag=PacketTag.SERVER_HELLO)

        #sends 3 times because UDP can drop packages
        reply = None
        broadcast_socket = None
        for _ in range(3):
            broadcast_socket = BroadcastSocket(
                hello_packet,
                response_handler=self.handle_leader_message,
                broadcast_port=10002,
                timeout_s= 1.0
            )
            broadcast_socket.start()

            try:
                reply = broadcast_socket.future.result(timeout=1.5)
            except Exception:
                reply = None

            if reply is not None:
                break

            time.sleep(0.2)

        if reply is None:
            print("No leader. My UUID", self.server_uuid)
            self.start_election()
        else:
            print("Answer received:", reply._tag, reply._content)
            if reply._tag == PacketTag.COORDINATOR:
                leader_uuid = reply._content["leader_uuid"]
                self._accept_leader(leader_uuid)

                #if im higher than
                if _uuid.UUID(self.server_uuid).int > _uuid.UUID(leader_uuid).int:
                    print("I have a higher uuid than leader start -> start Bully")
                    self.start_election()

            else:
                print("I shouldn't get a replay -> start election")
                self.start_election()


        while not self._is_stopped:
            now = time.monotonic()

            self._process_incoming_messages()
            self._update_game_states()
            self._send_outgoing_messages()

            #self.connection_manager.tick_client_heartbeat(now)

            # follower: leader timeout => election
            if not self.is_leader and self.leader_uuid is not None:
                if now - self._last_leader_seen > self.HEARTBEAT_TIMEOUT:
                    self.leader_uuid = None
                    self.start_election()

            # leader sends heartbeat
            if self.is_leader and now >= self._next_hb:
                self._next_hb += self.HEARTBEAT_INTERVAL
                hb = Packet(LeaderHeartbeat(leader_uuid=self.server_uuid), tag=PacketTag.LEADER_HEARTBEAT)
                self._fire_broadcast(hb, tries=1, timeout_s=0.15)

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
        def gt(a: str, b: str) -> bool:
            return _uuid.UUID(a).int > _uuid.UUID(b).int

        match packet._tag:

            # new Server asks: is there a leader
            case PacketTag.SERVER_HELLO:
                sender_uuid = packet._content["uuid"]

                # If there is a leader send Coordinator
                if self.leader_uuid is not None:
                    return Packet(
                        CoordinatorMessage(leader_uuid=self.leader_uuid),
                        tag=PacketTag.COORDINATOR
                    )

                # if i dont know leader or im the lader myself:
                if self.is_leader:
                    return Packet(
                        CoordinatorMessage(leader_uuid=self.server_uuid),
                        tag=PacketTag.COORDINATOR
                    )
                # start election else
                return None

                # Election larger id answers
            case PacketTag.ELECTION:
                candidate = packet._content["candidate_uuid"]
                if gt(self.server_uuid, candidate):
                    # to start leader selection simultaniously with sending ok back
                    Thread(target=self.start_election, daemon=True).start()
                    return Packet(OkMessage(responder_uuid=self.server_uuid), tag=PacketTag.OK)

                # No answer smaller UUID
                return None

            #announce new leader
            case PacketTag.COORDINATOR:
                leader_uuid = packet._content["leader_uuid"]
                self._accept_leader(leader_uuid)

                # if im higher than announced leader. Imma take over
                if gt(self.server_uuid, leader_uuid):
                    Thread(target=self.start_election, daemon=True).start()

                return None

            # if leader lives: but leader has lower uuid than own uuid start new election
            case PacketTag.LEADER_HEARTBEAT:
                leader_uuid = packet._content["leader_uuid"]
                self._last_leader_seen = time.monotonic()

                if self.leader_uuid != leader_uuid:
                    self._accept_leader(leader_uuid)

                if gt(self.server_uuid, leader_uuid):
                    Thread(target=self.start_election, daemon=True).start()

                return None

            case _:
                return None

    def _fire_broadcast(self, packet: Packet, tries: int = 2, timeout_s: float = 0.25):
        # to not block main thread
        for _ in range(tries):
            BroadcastSocket(packet, broadcast_port=10002, timeout_s=timeout_s).start()

    def _broadcast_and_wait_one(self, packet: Packet, timeout_s: float) -> Packet | None:
        bs = BroadcastSocket(packet, broadcast_port=10002, timeout_s=timeout_s)
        bs.start()
        return bs.future.result(timeout=timeout_s + 0.5)

    def _accept_leader(self, leader_uuid: str):
        self.leader_uuid = leader_uuid
        self.is_leader = (leader_uuid == self.server_uuid)
        self.election_in_progress = False
        self._last_leader_seen = time.monotonic()
        print("Leader is:", self.leader_uuid, "me?", self.is_leader)

    def _become_leader(self):
        self.leader_uuid = self.server_uuid
        self.is_leader = True
        self.election_in_progress = False
        self._last_leader_seen = time.monotonic()

        coord_packet = Packet(
            CoordinatorMessage(leader_uuid=self.server_uuid),
            tag=PacketTag.COORDINATOR
        )
        self._fire_broadcast(coord_packet, tries=3, timeout_s=0.25)
        print("I am the new leader:", self.server_uuid)

    def start_election(self):
        if self.election_in_progress:
            return
        self.election_in_progress = True
        self.is_leader = False
        self.leader_uuid = None

        election_packet = Packet(
            ElectionMessage(candidate_uuid=self.server_uuid),
            tag=PacketTag.ELECTION
        )

        #send multiple times because of UDP
        ok_received = False
        for _ in range(3):
            reply = self._broadcast_and_wait_one(election_packet, timeout_s=self.ELECTION_OK_WAIT)
            if reply is not None and reply._tag == PacketTag.OK:
                ok_received = True
                break
            time.sleep(0.15)

        if not ok_received:
            #nobody higher lives im leader
            self._become_leader()
            return

        # There is higher UUID wait for Cordinator
        deadline = time.monotonic() + self.COORDINATOR_WAIT
        while time.monotonic() < deadline and self.leader_uuid is None:
            time.sleep(0.05)

        # if nobody announces leader => new election
        if self.leader_uuid is None or self.is_leader:
            self.election_in_progress = False
            self.start_election()

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
