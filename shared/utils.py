from datetime import datetime

class Debug:
    ENABLED = True

    @staticmethod
    def log(message: str, *prefixes: str):
        if Debug.ENABLED:
            timestamp = datetime.now().strftime('%H:%M:%S:%f')[:-3]
            print(f"[{timestamp}]", end="")
            for prefix in prefixes:
                print(f"[{prefix}]", end="")
            print(" " + message)

