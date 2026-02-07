from multiprocessing import Event

from server_loop import ServerLoop
from shared.utils import Debug

if __name__ == '__main__':
    server_loop = None
    try:
        server_loop = ServerLoop()
        server_loop.run()
    except KeyboardInterrupt:
        Debug.log(f"Shutting down server...")
        #server_loop.stop()
