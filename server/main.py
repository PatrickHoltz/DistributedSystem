import socket
import shared.data as data
import shared.sockets as sockets


def handle_login(content: dict, address: tuple[str, int]):
    try:
        login_data = data.LoginData(**content)
        print(f"Login registered: {login_data.username}")
        response = sockets.Packet(data.StringMessage(f"Welcome back {login_data.username}!"))
        return response
    except TypeError:
        print("That is not a login message.")


login_listerner = sockets.BroadcastListener(on_message=handle_login)
login_listerner.start()
login_listerner.join()