import random
from uuid import uuid4
from multicast import Multicast
from server_loop import ServerLoop

import time

if __name__ == '__main__':
    #server_loop = ServerLoop()
    test = Multicast(uuid4())
    time.sleep(3)
    test.cast_msg("HELLO")
    time.sleep(3)
    test.cast_msg("FOO")
    test.cast_msg("BAR")
    
    i = 0
    while(True):
        time.sleep(random.uniform(1, 3))
        test.cast_msg("MSG" + str(i))
        i += 1
