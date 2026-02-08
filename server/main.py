import random
from uuid import uuid4
from multicast import Multicaster
from server_loop import ServerLoop
from shared.utils import Debug

import time

if __name__ == '__main__':
    server_loop = None
    try:
        server_loop = ServerLoop()
        server_loop.run()
    except KeyboardInterrupt:
        Debug.log(f"Shutting down server...")
        server_loop.stop()
