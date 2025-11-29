from multiprocessing import Process
import socket

class Broadcast(Process):

    def  __init__(self, )

    def run(self):
        broadcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast.sendto()