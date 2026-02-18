#!/usr/bin/env python3
import os, time, json, sys
from datetime import datetime

# allow: from shared.state import read_state
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.state import read_state

# ---- State file ----
GROW_STATE_FILE = "data/growlight_state.json"
LOCK_FILE = GROW_STATE_FILE + ".lock"

LED_PIN   = int(os.getenv("GROWLIGHT_PIN", "12"))   # ✅ keep GPIO12
LED_COUNT = int(os.getenv("GROWLIGHT_COUNT", "14"))
FPS       = float(os.getenv("GROWLIGHT_FPS", "2"))  # ✅ keep low; stable + less SD writes

# IMPORTANT:
# - We DO NOT force enabled True/False at startup.
# - We preserve whatever last state was stored in the file.
# - Defaults only apply if file missing/corrupt.
DEFAULTS = {
    "enabled": False,              # safe default ONLY if file is missing
    "mode": "SCHEDULE",            # SCHEDULE | SENSOR | MANUAL
    "brightness": 0.40,            # 0..1
    "target_680_pct": 55.0,        # SENSOR mode target wavelength %
    "schedule": {"on": "06:00", "off": "22:00"},
    "manual_on": False,
    "status": "OFF",               # ON/OFF
    "note": "",
    "updated_at": "",              # ISO
    "last_tick": "",               # ISO (optional)
}

# -------------------------
# File lock helpers
# -------------------------
def _with_lock(fn):
    os.makedirs(os.path.dirname(LOCK_FILE) or ".", exist_ok=True)
    with open(LOCK_FILE, "a") as lockf:
        try:
            import fcntl
            fcntl.flock(lockf, fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            return fn()
        finally:
            try:
                import fcntl
                fcntl.flock(lockf, fcntl.LOCK_UN)
            except Exception:
                pass

def _read_state_unlocked():
    try:
        with open(GROW_STATE_FILE, "r") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def _atomic_write(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def _ok_hhmm(x, fallback):
    try:
        x = str(x).strip()
        datetime.strptime(x, "%H:%M")
        return x
    except Exception:
        return fallback

def _ensure(st):
    """
    Normalize + sanitize WITHOUT nuking last saved state.
    Defaults apply only for missing keys.
    """
    if not isinstance(st, dict):
        st = {}

    out = dict(DEFAULTS)
    out.update(st)

    out["enabled"] = bool(out.get("enabled", False))

    mode = str(out.get("mode") or "SCHEDULE").upper().strip()
    if mode not in ("SCHEDULE", "SENSOR", "MANUAL"):
        mode = "SCHEDULE"
    out["mode"] = mode

    try:
        b = float(out.get("brightness", 0.40))
    except Exception:
        b = 0.40
    out["brightness"] = max(0.0, min(1.0, b))

    try:
        t = float(out.get("target_680_pct", 55.0))
    except Exception:
        t = 55.0
    out["target_680_pct"] = max(0.0, min(100.0, t))

    sched = out.get("schedule") if isinstance(out.get("schedule"), dict) else {}
    on_t  = _ok_hhmm(sched.get("on", "06:00"), "06:00")
    off_t = _ok_hhmm(sched.get("off", "22:00"), "22:00")
    out["schedule"] = {"on": on_t, "off": off_t}

    out["manual_on"] = bool(out.get("manual_on", False))

    st_status = str(out.get("status") or "OFF").upper().strip()
    if st_status not in ("ON", "OFF"):
        st_status = "OFF"
    out["status"] = st_status

    out["note"] = str(out.get("note") or "")

    ua = str(out.get("updated_at") or "").strip()
    if ua:
        try:
            datetime.fromisoformat(ua)
        except Exception:
            ua = ""
    out["updated_at"] = ua

    lt = str(out.get("last_tick") or "").strip()
    if lt:
        try:
            datetime.fromisoformat(lt)
        except Exception:
            lt = ""
    out["last_tick"] = lt

    return out

def load_state():
    return _with_lock(lambda: _ensure(_read_state_unlocked()))

def save_state(st):
    st = _ensure(st)
    def _w():
        _atomic_write(GROW_STATE_FILE, st)
        return st
    return _with_lock(_w)

# -------------------------
# LED backend
# -------------------------
class LedBackend:
    def __init__(self, gpio_pin: int, count: int):
        self.pin = gpio_pin
        self.count = count
        self.ok = False
        self.note = ""
        self._pixels = None
        self._last = None

        try:
            import board
            import neopixel

            # ✅ Raspberry Pi safe mapping order:
            # 1) board.D12 (some installs expose Pi pins as Dxx)
            # 2) board.GPIO12 (newer adafruit_blinka style)
            # 3) fallback to D18 if the env/board mapping is weird (but we still keep pin=12)
            pin_obj = getattr(board, f"D{gpio_pin}", None)
            if pin_obj is None:
                pin_obj = getattr(board, f"GPIO{gpio_pin}", None)

            if pin_obj is None:
                # last fallback to a known-working default mapping
                # (does NOT change your LED_PIN variable; it just prevents crash)
                pin_obj = getattr(board, "D18", None)

            if pin_obj is None:
                raise RuntimeError("No valid board pin object found for NeoPixel")

            self._pixels = neopixel.NeoPixel(
                pin_obj,
                count,
                brightness=1.0,      # we control brightness manually
                auto_write=False,
                pixel_order=neopixel.GRB
            )
            self.ok = True
            self.note = f"neopixel({pin_obj})"
        except Exception as e:
            self.note = f"no neopixel: {e}"
            self.ok = False

    def set_all(self, rgb, brightness01):
        if not self.ok:
            return

        r, g, b = rgb
        br = max(0.0, min(1.0, float(brightness01)))

        rr = int(max(0, min(255, r * br)))
        gg = int(max(0, min(255, g * br)))
        bb = int(max(0, min(255, b * br)))

        new = (rr, gg, bb)
        if new == self._last:
            return
        self._last = new

        self._pixels.fill(new)
        self._pixels.show()

    def off(self):
        self.set_all((0, 0, 0), 0.0)

def _time_in_window(now_hhmm, on_hhmm, off_hhmm):
    n = datetime.strptime(now_hhmm, "%H:%M")
    a = datetime.strptime(on_hhmm, "%H:%M")
    b = datetime.strptime(off_hhmm, "%H:%M")
    if a <= b:
        return a <= n < b
    return n >= a or n < b

def main():
    os.makedirs("data", exist_ok=True)

    backend = LedBackend(LED_PIN, LED_COUNT)

    # red-heavy = closest to 680nm you can get from WS2812B
    base_rgb = (255, 0, 0)

    print(f"🟢 growlight_daemon starting pin=GPIO{LED_PIN} count={LED_COUNT} backend={backend.note}")

    # Ensure state file exists; preserve last state if it exists.
    st = load_state()
    if not os.path.exists(GROW_STATE_FILE):
        st["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(st)
    else:
        # normalize only if sanitize would change it
        norm = _ensure(st)
        if json.dumps(norm, sort_keys=True) != json.dumps(st, sort_keys=True):
            norm["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_state(norm)

    period = 1.0 / max(1.0, FPS)
    last_written = None

    while True:
        st = load_state()

        st["last_tick"] = datetime.now().isoformat(timespec="seconds")

        enabled = bool(st.get("enabled"))
        mode = st.get("mode", "SCHEDULE")
        brightness = float(st.get("brightness", 0.40))
        sched = st.get("schedule", {}) or {}
        on_t = sched.get("on", "06:00")
        off_t = sched.get("off", "22:00")

        desired_on = False
        note = ""

        # MASTER OFF
        if not enabled:
            desired_on = False
            note = "disabled"

        elif mode == "MANUAL":
            desired_on = bool(st.get("manual_on"))
            note = "manual"

        elif mode == "SCHEDULE":
            now_hhmm = datetime.now().strftime("%H:%M")
            desired_on = _time_in_window(now_hhmm, on_t, off_t)
            note = f"schedule {on_t}-{off_t}"

        elif mode == "SENSOR":
            payload = read_state() or {}
            sensors = payload.get("sensors", {}) if isinstance(payload, dict) else {}
            wl = sensors.get("wavelength", {}) if isinstance(sensors, dict) else {}

            w680 = None
            if isinstance(wl, dict) and ("680" in wl):
                try:
                    w680 = float(wl.get("680"))
                except Exception:
                    w680 = None

            target = float(st.get("target_680_pct", 55.0))

            if w680 is None:
                # don’t ramp blindly; keep current brightness
                desired_on = True
                note = "sensor 680 missing"
            else:
                err = target - w680
                step = 0.0025 * err
                step = max(-0.02, min(0.02, step))  # clamp per tick
                brightness = max(0.0, min(1.0, brightness + step))
                st["brightness"] = brightness

                desired_on = True
                note = f"sensor 680={w680:.1f}% target={target:.1f}%"

        # apply LEDs
        if backend.ok and desired_on:
            backend.set_all(base_rgb, brightness)
            st["status"] = "ON"
        else:
            if backend.ok:
                backend.off()
            st["status"] = "OFF"

        st["note"] = note if backend.ok else ("LED backend missing: " + backend.note)

        st_norm = _ensure(st)
        serialized = json.dumps(st_norm, sort_keys=True)

        if serialized != last_written:
            st_norm["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_state(st_norm)
            last_written = json.dumps(_ensure(st_norm), sort_keys=True)

        time.sleep(period)

if __name__ == "__main__":
    main()