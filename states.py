from enum import Enum, auto

class State(Enum):
    NONE = auto()
    WAIT_ADDRESS = auto()
    WAIT_PHONE = auto()
    WAIT_VOLUME = auto()
    CONFIRM = auto()
