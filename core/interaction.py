from PySide6.QtCore import QObject, Signal
import threading

class InteractionBridge(QObject):
    """
    Bridge between worker threads (skills) and the main UI thread.
    Allows worker threads to request user confirmation blocking-ly.
    """
    request_confirmation_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self._event = threading.Event()
        self._result = False

    def ask_user(self, message: str) -> bool:
        """
        Called by worker thread to ask for user confirmation.
        Blocks until the UI thread responds.
        """
        self._event.clear()
        self.request_confirmation_signal.emit(message)
        self._event.wait() # Block until UI responds
        return self._result

    def respond(self, result: bool):
        """
        Called by UI thread to provide the response.
        """
        self._result = result
        self._event.set()

# Global instance
bridge = InteractionBridge()

def ask_user(message: str) -> bool:
    """Helper function to be used by skills"""
    return bridge.ask_user(message)
