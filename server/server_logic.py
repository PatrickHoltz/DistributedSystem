import uuid as _uuid
from shared.sockets import TCPConnection, Packet, PacketTag, BroadcastListener, BroadcastSocket
from shared.sockets import TCPConnection, Packet, PacketTag, BroadcastListener
from shared.data import *
import multiprocessing as mp
from typing import TypeVar
from threading import Thread, Timer
import time


class GameStateManager:
    '''Manager of the overall game state.'''
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
        '''Creates a new player entry if it does not exist and marks the player as online in all cases.'''
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
    '''Maintains active client connections and handles new logins using a broadcast listener.'''
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

    def _add_connection(self, username: str, communicator: 'ClientCommunicator'):
        '''Adds a new active connection and registers it in the game state manager.'''
        self.active_connections[username] = communicator
        self.last_seen[username] = time.monotonic()
        communicator.start()
        self.server_loop.game_state_manager.login_player(username)

    def remove_connection(self, username: str):
        '''Closes an active connection.'''
        if username in self.active_connections:
            self.active_connections[username].terminate()
            del self.active_connections[username]
        self.last_seen.pop(username, None)
        self.server_loop.game_state_manager.logout_player(username)

    def mark_seen(self, username: str):
        self.last_seen[username] = time.monotonic()

    def tick_client_heartbeat(self, now: float):
        '''
        - Sends in an intervall ping message to all connected Clients
        - Checks when the client was last seen (self.last_seen[username]) and removes connection if client didn't answer in CLIENT_HEARTBEAT_TIMEOUT time
        '''
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
                login_data = LoginData(**packet._content)
                print(f"Login registered: {login_data.username}")
                self._add_connection(login_data.username,
                                    ClientCommunicator(address, self.server_loop))
                game_state_update = self.server_loop.game_state_manager.get_state_update(login_data.username)
                response = Packet(game_state_update, tag=PacketTag.PLAYERGAMESTATE)
                return response
            except TypeError:
                print("Invalid login data received.")
        return None


class ServerLoop(Thread):
    '''Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick.'''
    MAX_MESSAGES_PER_TICK = 50

    def __init__(self):
        super().__init__()
        self.server_uuid = str(_uuid.uuid4())

        self.connection_manager = ConnectionManager(self)
        self.game_state_manager = GameStateManager(self)

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

    def run(self):
        hello_packet = Packet(ServerHello(uuid=self.server_uuid), tag=PacketTag.SERVER_HELLO)

        #sends 3 times because UDP can drop packages
        reply = None
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

            self.connection_manager.tick_client_heartbeat(now)

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
        '''Writes a given packet into the outgoing queue for all connected clients.'''
        for username in self.connection_manager.active_connections.keys():
            self.out_queue.put((username, packet))

    def _update_game_states(self):
        '''Writes game state updates for all connected clients into the outgoing queue.'''
        for username in self.connection_manager.active_connections.keys():
            game_state_update = self.game_state_manager.get_state_update(username)
            self.out_queue.put((username, Packet(game_state_update, tag=PacketTag.PLAYERGAMESTATE)))

    def _process_incoming_messages(self):
        processed = 0
        while not self.in_queue.empty() and processed < self.MAX_MESSAGES_PER_TICK:
            username, packet = self.in_queue.get()
            processed += 1

            self.connection_manager.mark_seen(username)

            match packet._tag:
                case PacketTag.CLIENT_PONG:
                    pass

                case PacketTag.CLIENT_PING:
                    self.out_queue.put((username, Packet(StringMessage("pong"), tag=PacketTag.CLIENT_PONG)))

                case PacketTag.ATTACK:
                    self.game_state_manager.apply_attack(username, packet._content['damage'])

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

class ClientCommunicator(mp.Process):
    ''' Communicates with a client using TCP connection. Incoming packets are filtered and their content is transformed into the appropriate data classes.
        Outgoing packets can be sent using the send method.
    '''
    T = TypeVar('T')

    def __init__(self, address: tuple[str, int], username: str, server_loop: ServerLoop):
        self.connection = TCPConnection(address)
        self._username = username
        self._server_loop = server_loop
        self._stop_event = mp.Event()
        self._recv_timeout = 2.0

    def run(self):
        self.connection.start()
        while not self._stop_event.is_set():
            # Wait for incoming packet
            packet = self.connection.get_packet(self._recv_timeout)
            if packet:
                self._handle_packet(packet)

    def _handle_packet(self, packet: Packet):
        '''Checks packet validity and provides the server loop with it.'''
        try:
            match packet._tag:
                case PacketTag.ATTACK:
                    typed_packet = self._get_typed_packet(packet, AttackData)

                case PacketTag.LOGOUT:
                    typed_packet = self._get_typed_packet(packet, LoginData)

                case PacketTag.CLIENT_PONG | PacketTag.CLIENT_PING:
                    typed_packet = self._get_typed_packet(packet, StringMessage)

                case _:
                    raise ValueError(f"Unknown packet tag received: {packet._tag}")
            self._server_loop.in_queue.put((self._username, typed_packet))
        except Exception as e:
            print("Error handling packet:", e)

    def _get_typed_packet(self, packet: Packet, content_type: T) -> Packet:
        # Validate packet content
        try:
            content = content_type(**packet._content)
            return Packet(content=content, tag=packet._tag)
        except TypeError:
            raise TypeError(
                f"Could not extract packet content of type {content_type}")