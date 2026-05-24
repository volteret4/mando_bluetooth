#!/usr/bin/env python3
"""
Client for the Beelink. Connects to the Raspberry Pi over TCP and
sends key commands to control the TV via Bluetooth HID.

Usage:
    python3 client.py <rpi-ip> [--port 5555]

Interactive mode key map (shown on startup).
You can also pipe JSON commands:
    echo '{"action":"key","key":"ENTER"}' | python3 client.py 192.168.1.100
"""

import argparse
import json
import readline  # noqa: F401 — enables arrow-key history in input()
import socket
import sys


RPI_DEFAULT_PORT = 5555

HELP = """
=== BT Keyboard Client ===
Navigation:
  up / down / left / right / enter / esc / back / home / pgup / pgdn

Media:
  play  pause  stop  next  prev  mute  volup  voldown

Numbers & letters:
  Type any single character: a-z, A-Z, 0-9, space

Combinations:
  ctrl+c   ctrl+v   ctrl+a   alt+f4   win+d  (etc.)

Special:
  type:<text>   — type a string, e.g.  type:hello world
  :q or exit    — quit

JSON raw mode (single line):
  {"action":"key","key":"UP"}
  {"action":"media","key":"VOLUP"}
  {"action":"combo","mod":"LALT","key":"F4"}
  {"action":"type","text":"hello"}
"""

# Shorthand aliases → JSON action dict
ALIASES: dict[str, dict] = {
    # Navigation
    "up":       {"action": "key", "key": "UP"},
    "down":     {"action": "key", "key": "DOWN"},
    "left":     {"action": "key", "key": "LEFT"},
    "right":    {"action": "key", "key": "RIGHT"},
    "enter":    {"action": "key", "key": "ENTER"},
    "ok":       {"action": "key", "key": "ENTER"},
    "esc":      {"action": "key", "key": "ESCAPE"},
    "escape":   {"action": "key", "key": "ESCAPE"},
    "back":     {"action": "key", "key": "ESCAPE"},
    "backspace":{"action": "key", "key": "BACKSPACE"},
    "del":      {"action": "key", "key": "DELETE"},
    "home":     {"action": "key", "key": "HOME"},
    "end":      {"action": "key", "key": "END"},
    "pgup":     {"action": "key", "key": "PAGEUP"},
    "pgdn":     {"action": "key", "key": "PAGEDOWN"},
    "tab":      {"action": "key", "key": "TAB"},
    # Media
    "play":     {"action": "media", "key": "PLAYPAUSE"},
    "pause":    {"action": "media", "key": "PLAYPAUSE"},
    "playpause":{"action": "media", "key": "PLAYPAUSE"},
    "stop":     {"action": "media", "key": "STOP"},
    "next":     {"action": "media", "key": "NEXT"},
    "prev":     {"action": "media", "key": "PREV"},
    "mute":     {"action": "media", "key": "MUTE"},
    "volup":    {"action": "media", "key": "VOLUP"},
    "voldown":  {"action": "media", "key": "VOLDOWN"},
    "vol+":     {"action": "media", "key": "VOLUP"},
    "vol-":     {"action": "media", "key": "VOLDOWN"},
    "+":        {"action": "media", "key": "VOLUP"},
    "-":        {"action": "media", "key": "VOLDOWN"},
    # Common TV shortcuts
    "info":     {"action": "key", "key": "F5"},
    "menu":     {"action": "key", "key": "F1"},
    "guide":    {"action": "key", "key": "F2"},
    "search":   {"action": "media", "key": "SEARCH"},
}


def parse_combo(text: str) -> dict | None:
    """Parse 'ctrl+c', 'alt+f4', 'win+d' style shortcuts."""
    parts = text.lower().split("+")
    if len(parts) < 2:
        return None
    mod_map = {
        "ctrl": "LCTRL", "control": "LCTRL",
        "shift": "LSHIFT",
        "alt": "LALT",
        "win": "LGUI", "super": "LGUI", "cmd": "LGUI",
        "rctrl": "RCTRL", "rshift": "RSHIFT", "ralt": "RALT",
    }
    mods = parts[:-1]
    key = parts[-1].upper()
    if len(mods) != 1:
        return None  # multi-modifier combos not supported yet
    mod = mod_map.get(mods[0])
    if mod is None:
        return None
    return {"action": "combo", "mod": mod, "key": key}


def parse_input(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None

    # Raw JSON
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"JSON error: {e}")
            return None

    # type:<text> shorthand
    if text.lower().startswith("type:"):
        return {"action": "type", "text": text[5:]}

    # Named alias
    lower = text.lower()
    if lower in ALIASES:
        return ALIASES[lower]

    # Single character → key press
    if len(text) == 1:
        ch = text.upper()
        if ch.isalnum():
            return {"action": "key", "key": ch}
        if text == " ":
            return {"action": "key", "key": "SPACE"}

    # combo: ctrl+x, alt+f4 …
    if "+" in text:
        combo = parse_combo(text)
        if combo:
            return combo

    print(f"Unknown command: {text!r}  (type 'help' for list)")
    return None


class RPiClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._buf = ""

    def connect(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((self.host, self.port))
        s.settimeout(None)
        self._sock = s
        print(f"Connected to {self.host}:{self.port}")

    def send(self, msg: dict) -> str:
        assert self._sock
        self._sock.sendall(json.dumps(msg).encode() + b"\n")
        # Read one response line
        while "\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection")
            self._buf += chunk.decode(errors="replace")
        line, self._buf = self._buf.split("\n", 1)
        return line.strip()

    def close(self) -> None:
        if self._sock:
            self._sock.close()


def interactive(client: RPiClient) -> None:
    print(HELP)
    print("Type a command (or 'help'):")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not text:
            continue
        if text in (":q", "quit", "exit", "bye"):
            print("Bye.")
            break
        if text == "help":
            print(HELP)
            continue

        msg = parse_input(text)
        if msg is None:
            continue

        try:
            resp = client.send(msg)
            print(f"  → {resp}")
        except (OSError, ConnectionError) as e:
            print(f"Connection error: {e}")
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="BT keyboard client for Beelink")
    parser.add_argument("host", help="Raspberry Pi IP address")
    parser.add_argument("--port", type=int, default=RPI_DEFAULT_PORT)
    args = parser.parse_args()

    client = RPiClient(args.host, args.port)
    try:
        client.connect()
    except (OSError, ConnectionError) as e:
        sys.exit(f"Cannot connect to {args.host}:{args.port} — {e}")

    try:
        if not sys.stdin.isatty():
            # Piped / scripted mode
            for line in sys.stdin:
                msg = parse_input(line)
                if msg:
                    print(client.send(msg))
        else:
            interactive(client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
