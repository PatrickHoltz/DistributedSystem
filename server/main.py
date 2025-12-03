import socket
import shared.dtos as dtos
import shared.packet as packet

# Create a UDP socket
server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Server application IP address and port
server_address = '127.0.0.1'
server_port = 10001

# Buffer size
buffer_size = 1024
message = 'Hi client! Nice to connect with you!'

# Bind socket to port
server_socket.bind((server_address, server_port))

print('Server up and running at {}:{}'.format(server_address, server_port))
while True:
    print('\nWaiting for a login...\n')
    data, address = server_socket.recvfrom(buffer_size)
    print('Received message from client: ', address)
    login_data: dtos.LoginData = packet.Packet.decode(data)._content
    print('Login: ', str(login_data))