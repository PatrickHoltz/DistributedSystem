import multiprocessing as mp
import threading
import time
import random

from uuid import UUID, uuid4
from threading import Thread, Lock, Timer

from server_logic import ConnectionManager, GameStateManager
from shared.data import *
from shared.sockets import Packet, PacketTag, BroadcastSocket, BroadcastListener
from shared.utils import Debug


class ServerLoop:
    """Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick."""
    MAX_MESSAGES_PER_TICK = 50

    BULLY_HEARTBEAT_INTERVAL = 1.0
    BULLY_HEARTBEAT_TIMEOUT = 4.0
    BULLY_ELECTION_OK_WAIT = 4.0
    BULLY_COORDINATOR_WAIT = 8.0
    TAKEOVER_COOLDOWN = 1.0
    _next_takeover_allowed = 0.0

    DEBUG = True

    GOSSIP_PLAYER_STATS_INTERVAL = 0.50
    GOSSIP_BOSS_SYNC_INTERVAL = 0.35

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

        self._election_lock = Lock()
        self._last_election_trigger = 0.0

        self._next_hb = time.monotonic() + self.BULLY_HEARTBEAT_INTERVAL
        self._last_leader_seen = time.monotonic()

        self._gossip_lock = Lock()
        self.boss_id: str = str(uuid4())

        self._stats_seq: int = 0
        self._last_stats_seq_by_server: dict[str, int] = {}
        self._next_gossip_stats = time.monotonic() + random.uniform(0.0, self.GOSSIP_PLAYER_STATS_INTERVAL)

        self._next_boss_sync = time.monotonic() + random.uniform(0.0, self.GOSSIP_BOSS_SYNC_INTERVAL)

        print(f"[SERVER] Started new server with UUID<{self.server_uuid}>")
        self.coordinator_timer: Timer | None = None
        self.election_timer: Timer | None = None
        self.bully_heartbeat_timer: Timer | None= None

        self.bully_listener = BroadcastListener(on_message=self.handle_leader_message,
                                                server_uuid=UUID(self.server_uuid).int)
        self.bully_listener.start()

        Debug.log(f"Started new server with UUID<{self.server_uuid}>", "Server")
        self.run()

    def run(self):
        self._restart_leader_heartbeat_timer()

        while not self._is_stopped:
            now = time.monotonic()

            # reset damage numbers for next loop iteration
            self.game_state_manager.latest_damage_numbers = []

            self._process_incoming_messages()

            # Leader computes boss HP/stage based on merged damage counters
            self._leader_apply_boss_progress()

            # Everyone gossips PlayerStats
            if now >= self._next_gossip_stats:
                self._next_gossip_stats += self.GOSSIP_PLAYER_STATS_INTERVAL
                self._broadcast_player_stats()

            if self.is_leader and now >= self._next_boss_sync:
                self._next_boss_sync += self.GOSSIP_BOSS_SYNC_INTERVAL
                self._broadcast_boss_sync()

            self._update_game_states()
            self._send_outgoing_messages()

            # self.connection_manager.tick_client_heartbeat(now)

            # leader sends heartbeat
            if self.is_leader and now >= self._next_hb:
                self._next_hb += self.BULLY_HEARTBEAT_INTERVAL
                hb = Packet(LeaderHeartbeat(leader_uuid=self.server_uuid), tag=PacketTag.BULLY_LEADER_HEARTBEAT,
                            server_uuid=UUID(self.server_uuid).int)
                # Debug.log(f"Sending leader heartbeat.", "LEADER")
                BroadcastSocket(hb, broadcast_port=10002, timeout_s=0.15, send_attempts=3).start()

            time.sleep(self.tick_rate)

    def handle_gossip_message(self, packet: Packet, address: tuple[str, int]):
        match packet.tag:
            case PacketTag.GOSSIP_PLAYER_STATS:
                sender_uuid = packet.content.get("server_uuid")
                seq = int(packet.content.get("seq", -1))
                stats = packet.content.get("stats") or {}
                players = stats.get("players") or {}

                if not sender_uuid:
                    return None

                last = self._last_stats_seq_by_server.get(sender_uuid, -1)
                if seq <= last:
                    return None
                self._last_stats_seq_by_server[sender_uuid] = seq

                self.game_state_manager.merge_player_stats(players)
                return None

            case PacketTag.GOSSIP_BOSS_SYNC:
                sync_leader = packet.content.get("leader_uuid")
                if self.leader_uuid is not None and sync_leader != self.leader_uuid:
                    return None

                new_boss_id = packet.content.get("boss_id")
                boss_dict = packet.content.get("boss")

                if not new_boss_id or not isinstance(boss_dict, dict):
                    return None

                new_boss = BossData(**boss_dict)

                with self._gossip_lock:
                    boss_changed = (new_boss_id != self.boss_id)
                    self.boss_id = new_boss_id
                    self.game_state_manager.set_boss(new_boss)

                    if boss_changed:
                        # no damage totals to reset
                        pass
                return None

            case _:
                return None

    def _broadcast_player_stats(self):
        """Broadcast gossip-safe PlayerStats."""
        self._stats_seq += 1
        stats = self.game_state_manager.get_player_stats()
        msg = GossipPlayerStats(server_uuid=self.server_uuid, seq=self._stats_seq, stats=stats)
        pkt = Packet(msg, tag=PacketTag.GOSSIP_PLAYER_STATS, uuid=UUID(self.server_uuid).int)
        self._fire_broadcast(pkt, tries=1, timeout_s=0.08)

    def _broadcast_boss_sync(self):
        if not self.is_leader:
            return
        boss = self.game_state_manager.get_boss()
        msg = GossipBossSync(boss_id=self.boss_id, leader_uuid=self.server_uuid, boss=boss)
        pkt = Packet(msg, tag=PacketTag.GOSSIP_BOSS_SYNC, uuid=UUID(self.server_uuid).int)
        self._fire_broadcast(pkt, tries=2, timeout_s=0.10)

    def _leader_apply_boss_progress(self):
        """Leader-only: if boss is dead, advance stage and announce a new boss_id"""
        if not self.is_leader:
            return

        with self._gossip_lock:
            boss = self.game_state_manager.get_boss()
            if boss.health > 0:
                return

            self.game_state_manager.advance_boss_stage()
            self.boss_id = str(uuid4())

            new_boss = self.game_state_manager.get_boss()
            self.multicast_packet(Packet(new_boss, tag=PacketTag.NEW_BOSS))
            self._broadcast_boss_sync()

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
                    damage = self.game_state_manager.apply_attack(username)
                    if self.is_leader and damage > 0:
                        with self._gossip_lock:
                            boss = self.game_state_manager.get_boss()
                            self.game_state_manager.set_boss_health(boss.health - int(damage))

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

    def _on_leader_heartbeat_timeout(self):
        """Timeout callback for when no leader heartbeat has been received in some time period."""
        if self.is_leader:
            return
        Debug.log(f"No leader heartbeat received. Starting election.", "SERVER", "BULLY")
        self._try_start_election()

    def _restart_leader_heartbeat_timer(self):
        """Restarts the timer responsible for recognizing a leader timeout."""
        if self.bully_heartbeat_timer and self.bully_heartbeat_timer.is_alive():
            self.bully_heartbeat_timer.cancel()
        self.bully_heartbeat_timer: Timer = Timer(self.BULLY_HEARTBEAT_TIMEOUT,
                                                  self._on_leader_heartbeat_timeout)
        self.bully_heartbeat_timer.start()

    def _try_start_election(self):
        """Start an election, but only if none is running and some time has passed since the last election."""
        now = time.monotonic()
        with self._election_lock:
            # don't start multiple elections concurrently
            if self.is_leader or self.election_in_progress:
                return

            # election timeout
            if now - self._last_election_trigger < self.TAKEOVER_COOLDOWN:
                return
            self._last_election_trigger = now

        self._start_election()

    def handle_leader_message(self, packet: Packet, address: tuple[str, int]):
        def gt(a: str, b: str) -> bool:
            return UUID(a).int > UUID(b).int

        match packet.tag:
            # election larger id answers
            case PacketTag.BULLY_ELECTION:
                candidate = packet.content["candidate_uuid"]

                Debug.log(f"Received election candidate <{candidate}>", "SERVER", "BULLY")

                if gt(self.server_uuid, candidate):
                    # if I am the leader, leader election is finished
                    if self.is_leader:
                        coord = Packet(
                            CoordinatorMessage(leader_uuid=self.server_uuid),
                            tag=PacketTag.BULLY_COORDINATOR,
                            server_uuid=UUID(self.server_uuid).int
                        )
                        BroadcastSocket(coord, timeout_s=0.15, send_attempts=5).start()

                    else:
                        Debug.log("My UUID is larger than candidate > replying ok", "SERVER", "BULLY")
                        # to start leader selection simultaneously with sending ok back
                        ok_packet = Packet(
                            OkMessage(responder_uuid=self.server_uuid),
                            tag=PacketTag.BULLY_OK,
                            server_uuid=UUID(self.server_uuid).int
                        )
                        return ok_packet

                        # DO NOT start a new election here
                    return None

                # I am the current leader and the incoming packet has a higher uuid => stepping down as leader
                if self.is_leader and gt(candidate, self.server_uuid):
                    Debug.log(f"Stronger candidate <{candidate}> contacted me. Stepping down.", "SERVER", "BULLY")

                    with self._election_lock:
                        self.is_leader = False
                        self.leader_uuid = None
                        self.election_in_progress = False
                return None

            # a leader is announced
            case PacketTag.BULLY_COORDINATOR:
                leader_uuid = packet.content["leader_uuid"]

                self._restart_leader_heartbeat_timer()

                # if gt(self.leader_uuid, leader_uuid):
                #    with self._election_lock:
                #        if self.election_in_progress:
                #            if self.DEBUG:
                #                print(f"[SERVER][{self.server_uuid}][BULLY] Ignoring weaker coordinator <{leader_uuid}> during my election")
                #            return None

                # not self leader
                if leader_uuid != self.server_uuid and gt(leader_uuid, self.server_uuid):
                    if self.coordinator_timer:
                        self.coordinator_timer.cancel()

                    self._accept_leader(leader_uuid)

                # if im higher than announced leader I will take over
                # if gt(self.server_uuid, leader_uuid):
                #     if (not self.is_leader) and (not self.election_in_progress) and (
                #             now >= self._last_election_trigger + self.BULLY_HEARTBEAT_TIMEOUT):
                #         Debug.log("Coordinator is weaker <{leader_uuid}> -> triggering takeover election", "SERVER",
                #                   "BULLY")
                #         self._try_start_election()

                return None

            # if leader lives: but leader has lower uuid than own uuid start new election
            case PacketTag.BULLY_LEADER_HEARTBEAT:
                leader_uuid = packet.content["leader_uuid"]

                # if leader_uuid == self.server_uuid:
                #     return None

                Debug.log(f"Received leader heartbeat from <{leader_uuid}>", "SERVER",
                          "BULLY")

                self._restart_leader_heartbeat_timer()

                # ignore weaker heartbeat
                if gt(self.server_uuid, leader_uuid):
                    with self._election_lock:
                        if self.election_in_progress:
                            Debug.log("Ignoring weaker heartbeat <{leader_uuid}> during my election", "SERVER",
                                      "BULLY")
                            return None

                # accept heartbeat
                elif self.leader_uuid != leader_uuid:
                    self._accept_leader(leader_uuid)

                return None

            case PacketTag.BULLY_OK:
                responder_uuid = packet.content["responder_uuid"]

                # filter messages to myself
                if self.server_uuid == responder_uuid:
                    return None

                if gt(responder_uuid, self.server_uuid):
                    if self.election_timer:
                        self.election_timer.cancel()

                    self.coordinator_timer = threading.Timer(self.BULLY_COORDINATOR_WAIT, self.on_coordinator_timeout)
                    self.coordinator_timer.start()

                return None

            case _:
                return None

    def on_coordinator_timeout(self):
        """Timeout callback for when no coordinator message was received after a bully ok"""
        self._try_start_election()

    def _accept_leader(self, leader_uuid: str):
        """Sets the leader state to this uuid."""
        with self._election_lock:
            changed = (self.leader_uuid != leader_uuid) or (self.is_leader != (leader_uuid == self.server_uuid))
            self.leader_uuid = leader_uuid
            self.is_leader = (leader_uuid == self.server_uuid)
            self.election_in_progress = False

        self._last_leader_seen = time.monotonic()
        if self.is_leader:
            # ensure the first heartbeat goes out quickly after leadership change
            self._next_hb = time.monotonic()

        if changed:
            Debug.log(f"Accepting leader <{self.leader_uuid}> {'(myself)' if self.is_leader else ''}", "SERVER",
                      "BULLY")

    def _become_leader(self):
        """Callback when no OK-Message has been received after an election broadcast."""
        with self._election_lock:
            self.leader_uuid = self.server_uuid
            self.is_leader = True
            self.election_in_progress = False

        self._last_leader_seen = time.monotonic()
        self._next_hb = time.monotonic() + self.BULLY_HEARTBEAT_INTERVAL

        coord_packet = Packet(
            CoordinatorMessage(leader_uuid=self.server_uuid),
            tag=PacketTag.BULLY_COORDINATOR,
            server_uuid=UUID(self.server_uuid).int
        )

        Debug.log(f"No one answered > declaring myself leader", "SERVER", "BULLY")
        BroadcastSocket(coord_packet, broadcast_port=10002, timeout_s=0.25, send_attempts=3).start()

    def _start_election(self):
        """Starts the election"""
        with self._election_lock:
            if self.election_in_progress:
                return

            self.election_in_progress = True
            self.is_leader = False
            self.leader_uuid = None

        election_packet = Packet(
            ElectionMessage(candidate_uuid=self.server_uuid),
            tag=PacketTag.BULLY_ELECTION,
            server_uuid=UUID(self.server_uuid).int
        )

        Debug.log(f"Starting leader election", "SERVER", "BULLY")

        BroadcastSocket(election_packet, broadcast_port=10002, timeout_s=0.15, send_attempts=3).start()

        self.election_timer = threading.Timer(self.BULLY_ELECTION_OK_WAIT, self._become_leader)
        self.election_timer.start()
