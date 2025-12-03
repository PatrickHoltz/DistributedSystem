import socket
import shared.data as data
import shared.sockets as sockets


def handle_login(content: dict, address: str):
    try:
        login_data = data.LoginData(**content)
        print(f"Login registered: {login_data.username}")
    except TypeError:
        print("That is not a login message.")


login_listerner = sockets.BroadcastListener(on_message=handle_login)
login_listerner.start()
login_listerner.join()