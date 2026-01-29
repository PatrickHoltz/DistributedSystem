from multicast import Multicast
from server_loop import ServerLoop

import time

if __name__ == '__main__':
    #server_loop = ServerLoop()
    test = Multicast()
    time.sleep(3)
    test.cast_msg("HELLO")
    time.sleep(3)
    test.cast_msg("FOO")
    test.cast_msg("BAR")
    time.sleep(3)
