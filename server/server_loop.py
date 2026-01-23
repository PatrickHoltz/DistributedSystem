import multiprocessing as mp
import time

from uuid import UUID, uuid4
from threading import Thread

from server_logic import ConnectionManager, GameStateManager
from shared.sockets import Packet, PacketTag, BroadcastSocket
from shared.data import *

class ServerLoop:
    """Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick."""
    MAX_MESSAGES_PER_TICK = 50

    BULLY_HEARTBEAT_INTERVAL = 2.0
    BULLY_HEARTBEAT_TIMEOUT = 6.0

    DEBUG = True

    def __init__(self):
        super().__init__()

        self.server_uuid = str(uuid4())

        self.connection_manager = ConnectionManager(self, UUID(self.server_uuid).int)
        self.game_state_manager = GameStateManager()

        self._is_stopped = False
        self.in_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.out_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.tick_rate = 0.1  # ticks per second

        self.leader_uuid: str | None = None
        self.is_leader: bool = False
        self.election_in_progress: bool = False

        self._next_hb = time.monotonic() + self.BULLY_HEARTBEAT_INTERVAL
        self._last_leader_seen = time.monotonic()

        print(f"[SERVER] Started new server with UUID<{self.server_uuid}>")
        self.run()

    def run(self):
        self.find_leader()

        while not self._is_stopped:
            now = time.monotonic()

            self._process_incoming_messages()
            self._update_game_states()
            self._send_outgoing_messages()

            #self.connection_manager.tick_client_heartbeat(now)

            # follower: leader timeout => election
            if not self.is_leader and self.leader_uuid is not None:
                if now - self._last_leader_seen > self.BULLY_HEARTBEAT_TIMEOUT:
                    self.leader_uuid = None
                    self.start_election()

            # leader sends heartbeat
            if self.is_leader and now >= self._next_hb:
                self._next_hb += self.BULLY_HEARTBEAT_INTERVAL
                hb = Packet(LeaderHeartbeat(leader_uuid=self.server_uuid), tag=PacketTag.BULLY_LEADER_HEARTBEAT)
                self._fire_broadcast(hb, tries=1, timeout_s=0.15)

            time.sleep(self.tick_rate)

    def stop(self):
        self._is_stopped = True
        
    def find_leader(self):
        hello_packet = Packet(ServerHello(uuid=self.server_uuid), tag=PacketTag.SERVER_HELLO, uuid=UUID(self.server_uuid).int)
        
        reply = None
        broadcast_socket = None
        for _ in range(3):  #sends 3 times because UDP can drop packages
            # TODO: is this ok because also 3 packages can drop?
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
            print(f"[SERVER][{self.server_uuid}] No leader found > starting election!")
            self.start_election()
        else:
            #if self.DEBUG:
            #    print(f"[SERVER][{self.server_uuid}] Got Answer for leader search: ( {reply.tag} | {reply.content} )")
                
            if reply.tag == PacketTag.BULLY_COORDINATOR:
                leader_uuid = reply.content["leader_uuid"]
                self._accept_leader(leader_uuid)

                if UUID(self.server_uuid).int > UUID(leader_uuid).int: # higher uuid then leader
                    print(f"[SERVER][{self.server_uuid}][BULLY] I have higher UUID > starting election!")
                    self.start_election()
            else:
                # TODO: what does this mean -> is this just a error catch case or part of bully
                print(f"[SERVER][{self.server_uuid}] I shouldn't get a replay > starting election!")
                self.start_election()
    
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
            return UUID(a).int > UUID(b).int

        match packet.tag:
            # new server asks: is there a leader
            case PacketTag.SERVER_HELLO:
                if self.DEBUG:
                    sender_uuid = packet.content["uuid"]
                    print(f"[SERVER][{self.server_uuid}][BULLY] Received new server hello <{sender_uuid}>")

                # if there is a leader send Coordinator
                if self.leader_uuid is not None:
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] Sending know leader UUID <{self.leader_uuid}>")

                    return Packet(
                        CoordinatorMessage(leader_uuid=self.leader_uuid),
                        tag=PacketTag.BULLY_COORDINATOR,
                        uuid=UUID(self.server_uuid).int
                    )

                # if I dont know leader or I am the leader myself:
                if self.is_leader:
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] Sending know leader UUID <{self.server_uuid}> (myself)")

                    return Packet(
                        CoordinatorMessage(leader_uuid=self.server_uuid),
                        tag=PacketTag.BULLY_COORDINATOR,
                        uuid=UUID(self.server_uuid).int
                    )

                return None # else start election

            # election larger id answers
            case PacketTag.BULLY_ELECTION:
                candidate = packet.content["candidate_uuid"]

                if self.DEBUG:
                    print(f"[SERVER][{self.server_uuid}][BULLY] Received election candidate <{candidate}>")

                if gt(self.server_uuid, candidate):
                    # to start leader selection simultaneously with sending ok back
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] UUID larger than mine > starting election + replying ok")            

                    Thread(target=self.start_election, daemon=True).start()
                    return Packet(
                        OkMessage(responder_uuid=self.server_uuid), 
                        tag=PacketTag.BULLY_OK,
                        uuid=UUID(self.server_uuid).int
                    )

                # no answer smaller UUID
                return None

            # announce new leader
            case PacketTag.BULLY_COORDINATOR:
                leader_uuid = packet.content["leader_uuid"]

                if self.DEBUG:
                    print(f"[SERVER][{self.server_uuid}][BULLY] Received new leader <{leader_uuid}>")

                self._accept_leader(leader_uuid)

                # if im higher than announced leader I will take over
                if gt(self.server_uuid, leader_uuid):
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] UUID smaller than mine > starting election")

                    Thread(target=self.start_election, daemon=True).start()

                return None

            # if leader lives: but leader has lower uuid than own uuid start new election
            case PacketTag.BULLY_LEADER_HEARTBEAT:
                leader_uuid = packet.content["leader_uuid"]
                
                if leader_uuid == self.server_uuid: # ignore message from myself
                    return None

                #if self.DEBUG:
                #    print(f"[SERVER][{self.server_uuid}][BULLY] Received heartbeat from leader <{leader_uuid}>")

                self._last_leader_seen = time.monotonic()

                if self.leader_uuid != leader_uuid:
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] UUID different > changing stored leader")

                    self._accept_leader(leader_uuid)

                if gt(self.server_uuid, leader_uuid):
                    if self.DEBUG:
                        print(f"[SERVER][{self.server_uuid}][BULLY] UUID smaller than mine > starting election")

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
        print(f"[SERVER][{self.server_uuid}][BULLY] Accepting leader <{self.leader_uuid}> {"(myself)" if self.is_leader else ""}")

    def _become_leader(self):
        self.leader_uuid = self.server_uuid
        self.is_leader = True
        self.election_in_progress = False
        self._last_leader_seen = time.monotonic()

        coord_packet = Packet(
            CoordinatorMessage(leader_uuid=self.server_uuid),
            tag=PacketTag.BULLY_COORDINATOR,
            uuid=UUID(self.server_uuid).int
        )
        self._fire_broadcast(coord_packet, tries=3, timeout_s=0.25)
        print(f"[SERVER][{self.server_uuid}][BULLY] No one answered > declaring myself leader")

    def start_election(self):
        if self.election_in_progress:
            return
        
        self.election_in_progress = True
        self.is_leader = False
        self.leader_uuid = None

        election_packet = Packet(
            ElectionMessage(candidate_uuid=self.server_uuid),
            tag=PacketTag.BULLY_ELECTION,
            uuid=UUID(self.server_uuid).int
        )

        ok_received = False
        for _ in range(3): # send multiple times because of UDP
            reply = self._broadcast_and_wait_one(election_packet, timeout_s=self.ELECTION_OK_WAIT)
            if reply is not None and reply.tag == PacketTag.BULLY_OK:
                ok_received = True
                break
            time.sleep(0.15)

        if not ok_received:
            # nobody higher lives -> I am leader
            self._become_leader()
            return

        # there is a higher UUID wait for coordinator
        deadline = time.monotonic() + self.COORDINATOR_WAIT
        while time.monotonic() < deadline and self.leader_uuid is None:
            time.sleep(0.05)

        # if nobody announces leader -> new election
        if self.leader_uuid is None or self.is_leader:
            self.election_in_progress = False
            self.start_election()
