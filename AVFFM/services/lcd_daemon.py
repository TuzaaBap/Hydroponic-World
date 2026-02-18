#!/usr/bin/env python3
import os, sys, time
from datetime import datetime
from RPLCD.i2c import CharLCD

# allow: from shared.state import read_state
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.state import read_state

# =========================
# LCD CONFIG (24x4)
# =========================
LCD_ADDR = int(os.getenv("LCD_ADDR", "0x27"), 16)
LCD_COLS = int(os.getenv("LCD_COLS", "24"))
LCD_ROWS = int(os.getenv("LCD_ROWS", "4"))
LCD_PORT = int(os.getenv("LCD_PORT", "1"))          # usually 1
LCD_EXP  = os.getenv("LCD_EXPANDER", "PCF8574")     # PCF8574

PAGE_SECONDS = float(os.getenv("LCD_PAGE_SECONDS", "5"))

lcd = CharLCD(
    i2c_expander=LCD_EXP,
    address=LCD_ADDR,
    port=LCD_PORT,
    cols=LCD_COLS,
    rows=LCD_ROWS,
    charmap='A00',
    auto_linebreaks=False
)

lcd.clear()
lcd.backlight_enabled = True

# =========================
# Helpers
# =========================
def clamp_str(s: str, width: int) -> str:
    s = "" if s is None else str(s)
    if len(s) > width:
        return s[:width]
    return s.ljust(width)

def fmt_num(v, suffix="", decimals=1):
    if v is None:
        return "--"
    try:
        x = float(v)
        if decimals == 0:
            return f"{int(round(x))}{suffix}"
        return f"{x:.{decimals}f}{suffix}"
    except Exception:
        return str(v)

def safe_get(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def write4(l1, l2, l3, l4):
    # 24x4 exactly
    lines = [l1, l2, l3, l4]
    lcd.clear()
    for r, text in enumerate(lines):
        lcd.cursor_pos = (r, 0)
        lcd.write_string(clamp_str(text, LCD_COLS))

# =========================
# Pages
# =========================
def page1(state):
    s = state.get("sensors", {}) if isinstance(state, dict) else {}
    ph   = safe_get(s, "ph", default=None)
    tds  = safe_get(s, "tds", default=None)
    w680 = safe_get(s, "wavelength", "680", default=None)

    # header clock
    now = datetime.now().strftime("%H:%M:%S")

    l1 = f"AVFFM  P1  {now}"
    l2 = f"pH  : {fmt_num(ph, '', 2)}"
    l3 = f"TDS : {fmt_num(tds, 'ppm', 0)}"
    l4 = f"680 : {fmt_num(w680, '%', 1)}"
    return (l1, l2, l3, l4)

def page2(state):
    s = state.get("sensors", {}) if isinstance(state, dict) else {}
    water = safe_get(s, "water_temp", default=None)
    air_t = safe_get(s, "dht", "temp", default=None)
    air_h = safe_get(s, "dht", "humidity", default=None)

    now = datetime.now().strftime("%H:%M:%S")

    l1 = f"AVFFM  P2  {now}"
    l2 = f"Water: {fmt_num(water, 'C', 1)}"
    l3 = f"Air T: {fmt_num(air_t, 'C', 1)}"
    l4 = f"Air H: {fmt_num(air_h, '%', 0)}"
    return (l1, l2, l3, l4)

PAGES = [page1, page2]

# =========================
# Main loop
# =========================
def main():
    idx = 0
    last_ok = time.time()

    while True:
        try:
            state = read_state() or {}
            lines = PAGES[idx % len(PAGES)](state)
            write4(*lines)

            idx += 1
            last_ok = time.time()
            time.sleep(PAGE_SECONDS)

        except Exception as e:
            # show error but don't crash
            write4(
                "LCD ERROR",
                str(e)[:LCD_COLS],
                "Retrying...",
                ""
            )
            time.sleep(2)

if __name__ == "__main__":
    main()