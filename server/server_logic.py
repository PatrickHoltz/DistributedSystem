from shared.sockets import TCPConnection, Packet, PacketTag, BroadcastListener
from shared.data import *
import multiprocessing as mp
from typing import TypeVar
from threading import Thread
import time


class GameStateManager:
    '''Manager of the overall game state.'''
    def __init__(self):
        pass


class ConnectionManager:
    '''Maintains active client connections and handles new logins using a broadcast listener.'''
    def __init__(self, server_loop: 'ServerLoop'):
        self.active_connections: dict[str, ClientCommunicator] = {}
        self.broadcast_listener = BroadcastListener(
            on_message=self.handle_login)
        self.broadcast_listener.start()
        self.server_loop = server_loop

    def add_connection(self, username: str, communicator: 'ClientCommunicator'):
        self.active_connections[username] = communicator
        communicator.start()
        # TODO: register logged in player in game state manager

    def remove_connection(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        # TODO: unregister logged in player in game state manager

    def handle_login(self, packet: Packet, address: tuple[str, int]):
        if packet._tag == PacketTag.LOGIN:
            try:
                login_data = LoginData(**packet._content)
                print(f"Login registered: {login_data.username}")
                self.add_connection(login_data.username,
                                    ClientCommunicator(address, self.server_loop))
                response = Packet(StringMessage(
                    f"Welcome back {login_data.username}!"))
                return response
            except TypeError:
                print("Invalid login data received.")
        return None


class ServerLoop(Thread):
    '''Main server loop handling incoming and outgoing messages. Runs a tick-based loop processing incoming messages and sending outgoing messages every tick.'''
    MAX_MESSAGES_PER_TICK = 50

    def __init__(self):
        self.connection_manager = ConnectionManager(self)
        self.game_state_manager = GameStateManager()

        self._is_stopped = False
        self.in_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.out_queue: mp.Queue[tuple[str, Packet]] = mp.Queue()
        self.tick_rate = 0.1  # ticks per second

    def stop(self):
        self._is_stopped = True

    def start(self):
        while not self._is_stopped:
            self._process_incoming_messages()
            self._send_outgoing_messages()
            time.sleep(self.tick_rate)

    def _process_incoming_messages(self):
        processed = 0
        while not self.in_queue.empty() and processed < self.MAX_MESSAGES_PER_TICK:
            username, packet = self.in_queue.get()
            # TODO: process different packet types and change state accordingly
            response = None
            self.out_queue.put((username, response))
            processed += 1

    def _send_outgoing_messages(self):
        processed = 0
        while not self.out_queue.empty() and processed < self.MAX_MESSAGES_PER_TICK:
            username, packet = self.out_queue.get()
            if username in self.connection_manager.active_connections:
                communicator = self.connection_manager.active_connections[username]
                communicator.send(packet)
            processed += 1


class ClientCommunicator(mp.Process):
    ''' Communicates with a client using TCP connection. Incoming packets are filtered and their content is transformed into the appropriate data classes.
        Outgoing packets can be sent using the send method.
    '''
    T = TypeVar('T')

    def __init__(self, address: tuple[str, int], server_loop: ServerLoop):
        self.handlers = {}
        self.connection = TCPConnection(address)
        self._stop_event = mp.Event()
        self._recv_timeout = 2.0

    def run(self):
        self.connection.start()
        while not self._stop_event.is_set():
            # Wait for incoming packet
            packet = self.connection.get_packet(self._recv_timeout)
            if packet:
                response = self._handle_packet(packet)
                if response:
                    self.connection.send(response)

    def register_handler(self, packet_type, handler):
        self.handlers[packet_type] = handler

    def _handle_packet(self, packet: Packet):
        try:
            match packet._tag:
                case PacketTag.ATTACK:
                    self._handle_attack(packet)
                case PacketTag.LOGOUT:
                    self._handle_logout(packet)
                case _:
                    print(f"Unknown packet type received: {packet._tag}")
        except Exception as e:
            print("Error handling packet:", e)

    def _get_packet_content(self, packet: Packet, content_type: T) -> T:
        # Validate packet content
        try:
            return content_type(**packet._content)
        except TypeError:
            raise TypeError(
                f"Could not extract packet content of type {content_type}")

    def _handle_attack(self, packet: Packet):
        # Process attack packet
        attack_data = self._get_packet_content(packet, AttackData)
        # access state

        pass

    def _handle_logout(self, packet: Packet):
        # Process logout packet

        pass
