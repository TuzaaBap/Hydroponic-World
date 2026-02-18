import json
import os
from threading import Lock

STATE_FILE = "data/latest_state.json"
_lock = Lock()

def write_state(data):
    with _lock:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)

def read_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)
