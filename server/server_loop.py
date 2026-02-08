import multiprocessing as mp
import threading
import time
import json
import random

from typing import Optional
from dataclasses import asdict

from uuid import UUID, uuid4
from threading import Lock, Timer

from multicast import Multicaster
from server_logic import ConnectionManager, GameStateManager
from shared.data import *
from shared.packet import PacketTag, Packet
from shared.sockets import BroadcastSocket, BroadcastListener
from server.server_sockets import Heartbeat
from shared.utils import Debug


class ServerLoop:
    """Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick."""
    MAX_MESSAGES_PER_TICK = 50

    SERVER_TO_LEADER_HEARTBEAT_INTERVAL = 1.0

    BULLY_HEARTBEAT_INTERVAL = 1.0
    BULLY_HEARTBEAT_TIMEOUT = 4.0
    BULLY_ELECTION_OK_WAIT = 4.0
    BULLY_COORDINATOR_WAIT = 8.0
    TAKEOVER_COOLDOWN = 1.0
    _next_takeover_allowed = 0.0

    SERVER_HEARTBEAT_TIMEOUT = 4.0

    DEBUG = True

    GOSSIP_PLAYER_STATS_INTERVAL = 0.50
    GOSSIP_MONSTER_SYNC_INTERVAL = 0.35

    def __init__(self):
        super().__init__()

        self.server_uuid = str(uuid4())

        self.connection_manager = ConnectionManager(self, self.server_uuid)
        self.game_state_manager = GameStateManager()

        self._is_stopped = False
        self.in_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.out_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.tick_rate = 0.2  # seconds between ticks

        self.leader_info: ServerInfo | None = None
        self.is_leader: bool = False
        self.election_in_progress: bool = False

        self._election_lock = Lock()
        self._last_election_trigger = 0.0

        self._next_hb = time.monotonic() + self.BULLY_HEARTBEAT_INTERVAL
        self._last_leader_seen = time.monotonic()

        self.monster_id: str = str(uuid4())

        self._stats_seq: int = 0
        self._last_stats_seq_by_server: dict[str, int] = {}
        self._next_gossip_stats = time.monotonic() + random.uniform(0.0, self.GOSSIP_PLAYER_STATS_INTERVAL)

        self._next_monster_sync = time.monotonic() + random.uniform(0.0, self.GOSSIP_MONSTER_SYNC_INTERVAL)

        self._heartbeat: Optional[Heartbeat] = None

        self.coordinator_timer: Timer | None = None
        self.election_timer: Timer | None = None
        self.bully_heartbeat_timer: Timer | None = None

        self.bully_listener = BroadcastListener(on_message=self.handle_leader_message,
                                                server_uuid=UUID(self.server_uuid).int)
        self.bully_listener.start()

        self.damage_multicaster = Multicaster(UUID(self.server_uuid), self._on_damage_multicast)
        self.damage_tracker = {}

        Debug.log(f"New server with UUID <{self.server_uuid}> started.",
                  "SERVER")
        self.run()

    def run(self):
        self._restart_leader_heartbeat_timer()

        while not self._is_stopped:
            start_time = time.time()
            now = time.monotonic()

            self._filter_server_view()

            # reset damage numbers for next loop iteration
            self.game_state_manager.latest_damage_numbers = []

            self._process_incoming_messages()
            
            # multicast aggregated damage to other servers
            # note: needs to be befor _leader_apply_monster_progress
            self.damage_multicaster.cast_msg(json.dumps({
                "type": "dmg",
                "uuid": self.server_uuid,
                "stage": self.game_state_manager.get_monster().stage,
                "damage": self.game_state_manager.overall_dmg,
            }))
            self.damage_multicaster.empty_msg_queue()

            # Leader computes monster HP/stage based on merged damage counters
            self._leader_apply_monster_progress()

            # Everyone gossips PlayerStats
            if now >= self._next_gossip_stats:
                self._next_gossip_stats += self.GOSSIP_PLAYER_STATS_INTERVAL
                self._broadcast_player_stats()

            self._update_game_states()
            self._send_outgoing_messages()

            # self.connection_manager.tick_client_heartbeat(now)

            time.sleep(max(0, self.tick_rate - (time.time() - start_time)))

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

            case _:
                return None

    def _broadcast_player_stats(self):
        """Broadcast gossip-safe PlayerStats."""
        self._stats_seq += 1
        stats = self.game_state_manager.get_player_stats()
        msg = GossipPlayerStats(server_uuid=self.server_uuid, seq=self._stats_seq, stats=stats)
        pkt = Packet(msg, tag=PacketTag.GOSSIP_PLAYER_STATS, server_uuid=UUID(self.server_uuid).int)
        BroadcastSocket(pkt, timeout_s=0.15, send_attempts=2).start()

    def _leader_apply_monster_progress(self):
        """Leader-only: if monster is dead, advance stage and announce a new monster_id"""
        if not self.is_leader:
            return

        monster = self.game_state_manager.get_monster()
        if monster.health > 0:
            return

        self.game_state_manager.advance_monster_stage()
        self.monster_id = str(uuid4())

        new_monster = self.game_state_manager.get_monster()
        self.damage_multicaster.cast_msg(json.dumps({
                "type": "monster",
                "monster": asdict(new_monster),
        }))
        
        self.deliver_packet_to_clients(Packet(new_monster, tag=PacketTag.NEW_MONSTER))

    def _on_damage_multicast(self, msg: str):
        data = json.loads(msg)
        type = data['type']
        
        match type:
            case 'dmg':
                uuid = data['uuid']
                damage = data['damage']
                stage = data['stage']

                if not stage or stage != self.game_state_manager.get_monster().stage:
                    print(f"IGNORING STAGE {stage} != {self.game_state_manager.get_monster().stage}")
                    return

                if uuid not in self.damage_tracker:
                    self.damage_tracker[uuid] = 0

                dif = damage - self.damage_tracker[uuid]

                if dif > 0:
                    #self.game_state_manager.apply_attack_from_other_server(dif)
                    self.damage_tracker[uuid] = damage
                    self.game_state_manager.latest_damage_numbers.append(dif)

                    dmg_sum = self.game_state_manager.overall_dmg
                    for _uuid, dmg in self.damage_tracker.items():
                        dmg_sum += dmg

                    self.game_state_manager.apply_attacks(dmg_sum)
                    print(f"DMG SUM: {dmg_sum}, Monster HP: {self.game_state_manager.get_monster().health}")

            case 'monster':
                new_monster = MonsterData(**data['monster'])
                print(f"NEW MONSTER: {new_monster}")
                
                self.game_state_manager.set_monster(new_monster)
                self.deliver_packet_to_clients(Packet(new_monster, tag=PacketTag.NEW_MONSTER))

    def stop(self):
        self._is_stopped = True

    def _filter_server_view(self):
        """Filters outdated servers from the server view"""
        old_len = len(self.connection_manager.server_view)
        now = time.monotonic()
        filtered_view = {k: v for k, v in self.connection_manager.server_view.items() if v.last_seen > now - self.SERVER_HEARTBEAT_TIMEOUT}
        self.connection_manager.server_view = filtered_view
        if len(filtered_view) != old_len:
            print(f"Server view size shrunk to {len(filtered_view)}")



    def deliver_packet_to_clients(self, packet: Packet):
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
                    self.game_state_manager.apply_attack(username)

                case PacketTag.LOGOUT:
                    self.connection_manager.remove_connection(username)
                    self.game_state_manager.logout_player(username)
                    Debug.log(f"Player '{username}' logged out.", "SERVER")
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
                            self.leader_info,
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
                        self.leader_info = None
                        self.election_in_progress = False
                return None

            # a leader is announced
            case PacketTag.BULLY_COORDINATOR:
                leader_info = ServerInfo(**packet.content)

                self._restart_leader_heartbeat_timer()

                # if gt(self.leader_uuid, leader_uuid):
                #    with self._election_lock:
                #        if self.election_in_progress:
                #            if self.DEBUG:
                #                print(f"[SERVER][{self.server_uuid}][BULLY] Ignoring weaker coordinator <{leader_uuid}> during my election")
                #            return None

                # not self leader
                if leader_info.server_uuid != self.server_uuid and gt(leader_info.server_uuid, self.server_uuid):
                    if self.coordinator_timer:
                        self.coordinator_timer.cancel()

                    self._accept_leader(leader_info)

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
                leader_info = ServerInfo(**packet.content)

                # if leader_uuid == self.server_uuid:
                #     return None

                #Debug.log(f"Received leader heartbeat from <{leader_info.server_uuid}>", "SERVER", "BULLY")

                self._restart_leader_heartbeat_timer()

                # ignore weaker heartbeat
                if gt(self.server_uuid, leader_info.server_uuid):
                    with self._election_lock:
                        if self.election_in_progress:
                            Debug.log(f"Ignoring weaker heartbeat <{leader_info.server_uuid}> during my election", "SERVER",
                                      "BULLY")
                            return None

                # accept heartbeat
                if self.leader_info is None or self.leader_info.server_uuid != leader_info.server_uuid:
                    self._accept_leader(leader_info)

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
                return self.handle_gossip_message(packet,address)

    def on_coordinator_timeout(self):
        """Timeout callback for when no coordinator message was received after a bully ok"""
        self._try_start_election()

    def _accept_leader(self, leader_info: ServerInfo):
        """Sets the leader state to this uuid."""
        old_leader_uuid = self.leader_info.server_uuid if self.leader_info else None

        with self._election_lock:
            self.leader_info = leader_info
            self.is_leader = (leader_info.server_uuid == self.server_uuid)
            self.election_in_progress = False
            self._last_leader_seen = time.monotonic()
        if self.is_leader:
            # ensure the first heartbeat goes out quickly after leadership change
            self._next_hb = time.monotonic()

        # leader changed
        if old_leader_uuid is None or old_leader_uuid != leader_info.server_uuid:
            self._on_leader_changed()
        Debug.log(f"Accepting leader <{self.leader_info.server_uuid}> {'(myself)' if self.is_leader else ''}", "SERVER",
                  "BULLY")

    def _become_leader(self):
        """Callback when no OK-Message has been received after an election broadcast."""
        with self._election_lock:
            self.leader_info = self.connection_manager.server_info
            self.is_leader = True
            self.election_in_progress = False

        self._last_leader_seen = time.monotonic()
        self._next_hb = time.monotonic() + self.BULLY_HEARTBEAT_INTERVAL
        self._on_leader_changed()

        coord_packet = Packet(
            self.connection_manager.server_info,
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
            self.leader_info = None

        election_packet = Packet(
            ElectionMessage(candidate_uuid=self.server_uuid),
            tag=PacketTag.BULLY_ELECTION,
            server_uuid=UUID(self.server_uuid).int
        )

        Debug.log(f"Starting leader election", "SERVER", "BULLY")

        BroadcastSocket(election_packet, broadcast_port=10002, timeout_s=0.15, send_attempts=3).start()

        self.election_timer = threading.Timer(self.BULLY_ELECTION_OK_WAIT, self._become_leader)
        self.election_timer.start()

    def get_heartbeat_packet(self) -> Packet:
        """Returns the correct heartbeat packet depending on weather this server is the leader or not."""
        server_info = self.connection_manager.server_info
        if self.is_leader:
            return Packet(server_info, tag=PacketTag.BULLY_LEADER_HEARTBEAT, server_uuid=UUID(server_info.server_uuid).int)

        else:
            return Packet(server_info, tag=PacketTag.SERVER_HEARTBEAT, server_uuid=UUID(server_info.server_uuid).int)

    def _on_leader_changed(self):
        """Callback when the leader changes. Initializes a new heartbeat based on being a leader or not."""
        if self._heartbeat:
            self._heartbeat.stop()

        if self.is_leader:
            # broadcast leader heartbeat
            self._heartbeat = Heartbeat(self.get_heartbeat_packet, self.BULLY_HEARTBEAT_INTERVAL)
        else:
            # udp heartbeat to leader
            self._heartbeat = Heartbeat(self.get_heartbeat_packet, self.SERVER_TO_LEADER_HEARTBEAT_INTERVAL, self.leader_info.ip, self.leader_info.udp_port)
        self._heartbeat.start()
