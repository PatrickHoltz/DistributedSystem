import socket
import shared.data as data
import shared.sockets as sockets


def handle_login(packet: sockets.Packet, address: tuple[str, int]):
    if packet._tag == sockets.PacketTag.LOGIN:
        try:
            login_data = data.LoginData(**packet._content)
            print(f"Login registered: {login_data.username}")
            response = sockets.Packet(data.StringMessage(f"Welcome back {login_data.username}!"))
            return response
        except TypeError:
            # invalid data received
            pass
    return None


login_listerner = sockets.BroadcastListener(on_message=handle_login)
login_listerner.start()
login_listerner.join()