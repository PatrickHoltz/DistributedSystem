"""Provides everything needed for multicasting messages"""
import json
import random
import signal
import socket
import struct
import time

from dataclasses import dataclass
from logging import warning
from multiprocessing import Process, Queue, Event
from queue import Empty
from threading import Thread, Lock
from typing import Callable
from uuid import UUID

from shared.utils import Debug

@dataclass
class MulticastPacket:
    """Basic data needed for every package"""
    type_id: int
    sender_uuid: UUID
    _raw_content: str

    FIX_SIZE: int = 16384

    @staticmethod
    def get_format_str() -> str:
        content_size = MulticastPacket.FIX_SIZE - 4 - 16
        return '!I16s' + str(content_size) + 's'

    def pack(self) -> bytes:
        uuid = self.sender_uuid.bytes
        chars = self._raw_content.encode('utf-8')
        data = struct.pack(MulticastPacket.get_format_str(), self.type_id, uuid, chars)

        if len(data) > MulticastPacket.FIX_SIZE:
            warning(f"MulticastPacket exceeds the set fixed size of {MulticastPacket.FIX_SIZE} (actual size: {len(data)})")

        return data

    @staticmethod
    def unpack(data: bytes) -> 'MulticastPacket':
        type_id, uuid_bytes, content_bytes= struct.unpack(MulticastPacket.get_format_str(), data)
        uuid = UUID(bytes=uuid_bytes)
        content = content_bytes.decode("utf-8").rstrip('\x00')

        return MulticastPacket(type_id, uuid, content)

class MulticastMessagePacket(MulticastPacket):
    """Contains all the data for a new message"""

    sequence_id: int
    content: str

    TYPE_ID = 1111

    def __init__(self, own_uuid: UUID, sequence_id: int, msg_content: str):
        data = {
            "msg_sequence_id": sequence_id,
            "msg_content": msg_content
        }
        MulticastPacket.__init__(self, self.TYPE_ID, own_uuid, json.dumps(data))

        self.sequence_id = sequence_id
        self.content = msg_content

    @classmethod
    def from_packet(cls, packet: MulticastPacket) -> 'MulticastMessagePacket | None':
        if packet.type_id != cls.TYPE_ID:
            warning(f"MulticastPacket type id does not match {cls.__class__.__name__}! (actual id: {packet.type_id})")
            return None

        data = json.loads(packet._raw_content)
        return cls(packet.sender_uuid, data['msg_sequence_id'], data['msg_content'])

class MulticastRequestMissingPacket(MulticastPacket):
    """Contains all the data for requesting a missed message"""

    msg_sender_uuid: UUID
    msg_sequence_id: int

    TYPE_ID = 2222

    def __init__(self, own_uuid: UUID, sender_uuid: UUID, sequence_id: int):
        data = {
            "msg_sender_uuid": sender_uuid.hex,
            "msg_sequence_id": sequence_id
        }
        MulticastPacket.__init__(self, self.TYPE_ID, own_uuid, json.dumps(data))

        self.msg_sender_uuid = sender_uuid
        self.msg_sequence_id = sequence_id

    @classmethod
    def from_packet(cls, packet: MulticastPacket) -> 'MulticastRequestMissingPacket | None':
        if packet.type_id != cls.TYPE_ID:
            warning(f"MulticastPacket type id does not match {cls.__class__.__name__}! (actual id: {packet.type_id})")
            return None

        data = json.loads(packet._raw_content)
        uuid = UUID(hex=data['msg_sender_uuid'])
        sequence_id = data['msg_sequence_id']
        return cls(packet.sender_uuid, uuid, sequence_id)

class MulticastHeartbeatPacket(MulticastPacket):
    """Contains all the data for a heartbeat"""

    heartbeat_id: int

    TYPE_ID = 1337

    def __init__(self, own_uuid: UUID, heartbeat_id: int):
        data = {
            "heartbeat_id": heartbeat_id,
        }
        MulticastPacket.__init__(self, self.TYPE_ID, own_uuid, json.dumps(data))

        self.heartbeat_id = heartbeat_id

    @classmethod
    def from_packet(cls, packet: MulticastPacket) -> 'MulticastHeartbeatPacket | None':
        if packet.type_id != cls.TYPE_ID:
            warning(f"MulticastPacket type id does not match {cls.__class__.__name__}! (actual id: {packet.type_id})")
            return None

        data = json.loads(packet._raw_content)
        heartbeat_id = data['heartbeat_id']
        return cls(packet.sender_uuid, heartbeat_id)

class MulticastReceiver(Thread):
    """Creates a new thread for handling incoming multicast packages"""

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int, stop_event: Event) -> None:
        super().__init__(daemon=True, name='MulticastReceiver')

        self.msg_queue = []
        self.lock = Lock()

        self.group = group
        self.port = port
        self._stop_event = stop_event

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', self.port))

    def run(self) -> None:
        config = struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, config)
        self.socket.settimeout(1)

        while not self._stop_event.is_set():
            data: bytes
            try:
                data = self.socket.recv(MulticastPacket.FIX_SIZE)
            except socket.timeout:
                continue
            
            msg = MulticastPacket.unpack(data)

            with self.lock:
                self.msg_queue.append(msg)

    def has_msgs(self) -> bool:
        return len(self.msg_queue) > 0

    def get(self) -> MulticastPacket | None:
        msg = None

        with self.lock:
            if len(self.msg_queue) > 0:
                msg = self.msg_queue.pop()

        return msg

class MulticastSender(Thread):
    """Creates a new thread for sending multicast packages"""

    # how many network hops the msg will take (see https://www.tldp.org/HOWTO/Multicast-HOWTO-6.html)
    MULTICAST_TTL = 1

    msg_queue: list[MulticastPacket]
    lock: Lock

    def __init__(self, group: str, port: int, stop_event: Event) -> None:
        super().__init__(daemon=True, name='MulticastSender')

        self.msg_queue = []
        self.lock = Lock()

        self.group = group
        self.port = port
        self._stop_event = stop_event

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.MULTICAST_TTL)

    def run(self) -> None:
        while not self._stop_event.is_set():
            if len(self.msg_queue) > 0:
                with self.lock:
                    msg = self.msg_queue.pop()

                msg_bytes = msg.pack()
                self.socket.sendto(msg_bytes, (self.group, self.port))

    def send(self, msg: MulticastPacket) -> None:
        with self.lock:
            self.msg_queue.append(msg)

class MulticasterProcess(Process):
    """Process that handles reliably ordered (FIFO) multicasts"""
    PORT = 5007
    DEBUG = False
    LOGGING = False
    OMISSION_CHANCE = 0

    HEARTBEAT_INTERVAL = 3

    group: str
    uuid: UUID

    in_queue: Queue
    out_queue: Queue

    _sender: MulticastSender
    _receiver: MulticastReceiver
    _receive_handler: Thread

    _sequence_number: int
    _received_tracker: dict[str, int]

    _hold_back_queue: list[MulticastMessagePacket]
    _received_msgs: dict[tuple[UUID, int], MulticastMessagePacket]
    
    _heartbeat_stamps: dict[str, tuple[int, float]]
    _next_heartbeat: float
    _heartbeat_id: int

    def __init__(self, group: str, uuid: UUID) -> None:
        super().__init__(daemon=False, name=f"Multicaster ({group})")

        self.group = group
        self.uuid = uuid

        self.in_queue = Queue()
        self.out_queue = Queue()

        self._sequence_number = 0
        self._received_tracker = {}
        self._hold_back_queue = []
        self._received_msgs = {}
        
        self._heartbeat_stamps = {}
        self._next_heartbeat = time.monotonic() - self.HEARTBEAT_INTERVAL
        self._heartbeat_id = 1
        
        self._stop_event = Event()

    def run(self) -> None:
        # needed to stop stdlib crashing on SIGINT
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        
        self._sender = MulticastSender(self.group, self.PORT, self._stop_event)
        self._receiver = MulticastReceiver(self.group, self.PORT, self._stop_event)
        self._receive_handler = Thread(target=self._receive_loop, args=())

        self._sender.start()
        self._receiver.start()
        self._receive_handler.start()

        while not self._stop_event.is_set():
            msg_str: str = ""
            try:
                msg_str = self.in_queue.get(timeout=1)
            except Empty:
                continue
            
            if self.LOGGING:
                with open(f"multicast_log-{self.uuid}.txt", "a") as f:
                    f.write(f"Sending package: {msg_str}\n")
            
            self._reliable_multicast(msg_str)
        
        self._sender.join()
        self._receiver.join()
        self._receive_handler.join()
    
    def stop(self) -> None:
        self._stop_event.set()

    def _receive_loop(self):
        while not self._stop_event.is_set():
            if self._receiver.has_msgs():
                msg = self._receiver.get()
                if msg is None:
                    continue
                
                if random.random() > 1 - self.OMISSION_CHANCE: # 0.0 <= random() < 1.0
                    if self.DEBUG:
                        Debug.log(f"Simulating package omission for {msg.type_id}", "MULTICASTER")
                    return

                if self.DEBUG:
                    Debug.log(f"Receiving package with type {msg.type_id}", "MULTICASTER")
                
                if self.LOGGING:
                    with open(f"multicast_log-{self.uuid}.txt", "a") as f:
                        f.write(f"Receiving package: {msg}\n")

                match(msg.type_id):
                    case MulticastMessagePacket.TYPE_ID:
                        msg = MulticastMessagePacket.from_packet(msg)
                        if msg is not None:
                            self._on_receive(msg)
                            self._clear_hold_back_queue(msg.sender_uuid)

                    case MulticastRequestMissingPacket.TYPE_ID:
                        msg = MulticastRequestMissingPacket.from_packet(msg)
                        if msg is not None:
                            key = (msg.msg_sender_uuid, msg.msg_sequence_id)
                            if key in self._received_msgs:
                                stored_msgs = self._received_msgs[key]
                                self._sender.send(stored_msgs)

                    case MulticastHeartbeatPacket.TYPE_ID:
                        msg = MulticastHeartbeatPacket.from_packet(msg)
                        if msg is not None:
                            last_id_seen = -1
                            if msg.sender_uuid in self._heartbeat_stamps:
                                last_id_seen, _ = self._heartbeat_stamps[msg.sender_uuid]

                            if msg.heartbeat_id > last_id_seen:
                                self._heartbeat_stamps[msg.sender_uuid] = (msg.heartbeat_id , time.monotonic())
            
            # send heartbeat if time and filter out due servers
            if time.monotonic() > self._next_heartbeat:
                self._next_heartbeat = time.monotonic() + self.HEARTBEAT_INTERVAL
                
                hb = MulticastHeartbeatPacket(self.uuid, self._heartbeat_id)
                self._heartbeat_id += 1
                self._sender.send(hb)
                
                now = time.monotonic()
                limit = 3 * self.HEARTBEAT_INTERVAL
                timeouted = []
                for uuid in self._heartbeat_stamps:
                    _id, stamp = self._heartbeat_stamps[uuid]
                    if now - stamp > limit:
                        timeouted.append(uuid)
                
                for uuid in timeouted:
                    del self._received_tracker[uuid.hex]
                    del self._heartbeat_stamps[uuid]

    # basic multicast
    def _on_receive(self, msg: MulticastMessagePacket) -> None:
        tracker = self._received_tracker.get(msg.sender_uuid.hex)
        if msg.sequence_id == tracker or tracker is None:
            self._received_tracker[msg.sender_uuid.hex] = msg.sequence_id + 1

            self._basic_deliver(msg)

        elif msg.sequence_id > tracker:
            missing_req = MulticastRequestMissingPacket(self.uuid, msg.sender_uuid, tracker)
            self._sender.send(missing_req)

            self._hold_back_queue.append(msg)

        # else ignore duplicate msg

    def _basic_multicast(self, content: str) -> None:
        msg = MulticastMessagePacket(self.uuid, self._sequence_number, content)

        self._sequence_number += 1
        self._sender.send(msg)

    def _basic_deliver(self, msg: MulticastMessagePacket) -> None:
        key = (msg.sender_uuid, msg.sequence_id)
        if key not in self._received_msgs:
            self._received_msgs[key] = msg

            if msg.sender_uuid != self.uuid:
                self._sender.send(msg)

            self._reliable_deliver(msg)

    def _reliable_multicast(self, content: str) -> None:
        data = {
            'received_tracker': self._received_tracker,
            'content': content
        }
        json_str = json.dumps(data)
        self._basic_multicast(json_str)

    def _reliable_deliver(self, msg: MulticastMessagePacket) -> None:
        if self.DEBUG:
            Debug.log(f"Delivering package: {msg.sequence_id} > {msg.content}", "MULTICASTER")

        if self.LOGGING:
            with open(f"multicast_log-{self.uuid}.txt", "a") as f:
                f.write(f"Delivering package: {msg}\n")

        data = json.loads(msg.content)
        if msg.sender_uuid == self.uuid:
            return # ignore messages from myself

        msg_str = data['content']
        self.out_queue.put_nowait(msg_str)

    def _clear_hold_back_queue(self, sender_uuid: UUID):
        while len(self._hold_back_queue) > 0:
            tracker = self._received_tracker.get(sender_uuid.hex)
            next_msg = None
            for msg in self._hold_back_queue:
                if msg.sender_uuid != sender_uuid:
                    continue

                if msg.sequence_id == tracker:
                    next_msg = msg
                    break

            if next_msg is not None:
                self._hold_back_queue.remove(next_msg)
                self._on_receive(next_msg)
            else:
                break

class Multicaster:
    """Class for sending and receiving reliably ordered (FIFO) multicasts"""
    on_msg_handler: Callable[[str], None]

    _multicaster_process: MulticasterProcess

    def __init__(self, uuid: UUID, on_msg_handler: Callable[[str], None], group: str = '224.0.0.1') -> None:
        self._multicaster_process = MulticasterProcess(group, uuid)
        self.on_msg_handler = on_msg_handler

        self._multicaster_process.start()

    def stop(self) -> None:
        self._multicaster_process.stop()
        self._multicaster_process.join()
        self._multicaster_process.close()
        Debug.log("Multicaster process stopped.")

    def cast_msg(self, msg: str) -> None:
        """Multicasts the message to the group"""
        self._multicaster_process.in_queue.put_nowait(msg)

    def empty_msg_queue(self) -> None:
        while not self._multicaster_process.out_queue.empty():
            msg_str = self._multicaster_process.out_queue.get()
            self.on_msg_handler(msg_str)
