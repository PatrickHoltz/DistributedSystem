from shared.data import LoginData
import shared.sockets as sockets



class LoginService:

    def login(self, username: str):
        print("Logging in...")
        # start listener (for response)


        # broadcast login message
        login_data = LoginData(username)
        packet = sockets.Packet(login_data)
        login_broadcast = sockets.BroadcastSocket(packet)
        login_broadcast.start()
        packet, addr = login_broadcast.get_response()
        print(f"Connected to {addr}")
        print(packet._content)
        login_broadcast.join()


    def register(login_data: LoginData):
        pass


login_service = LoginService()