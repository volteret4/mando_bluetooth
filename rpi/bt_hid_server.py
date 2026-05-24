#!/usr/bin/env python3
"""
Bluetooth HID keyboard server for Raspberry Pi 4.
Presents as a Bluetooth keyboard to the TV and accepts key commands
from the Beelink over TCP (port 5555).

Usage:
    sudo python3 bt_hid_server.py [--host 0.0.0.0] [--port 5555]

Protocol (TCP, newline-delimited JSON):
    {"action": "key",   "key": "UP"}             -> navigation key
    {"action": "media", "key": "VOLUP"}           -> consumer/media key
    {"action": "combo", "mod": "LCTRL", "key": "C"} -> modifier + key
    {"action": "type",  "text": "hello"}          -> type a string
"""

import argparse
import json
import logging
import socket
import subprocess
import sys
import threading
import time

from hid import (
    HID_DESCRIPTOR,
    KEY_CODES,
    MEDIA_CODES,
    MOD_NONE,
    MOD_LCTRL, MOD_LSHIFT, MOD_LALT, MOD_LGUI,
    MOD_RCTRL, MOD_RSHIFT, MOD_RALT, MOD_RGUI,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

HID_CONTROL_PSM   = 0x11  # 17
HID_INTERRUPT_PSM = 0x13  # 19

MOD_MAP = {
    "LCTRL": MOD_LCTRL, "LSHIFT": MOD_LSHIFT,
    "LALT":  MOD_LALT,  "LGUI":   MOD_LGUI,
    "RCTRL": MOD_RCTRL, "RSHIFT": MOD_RSHIFT,
    "RALT":  MOD_RALT,  "RGUI":   MOD_RGUI,
}

# ASCII→keycode mapping for type action (lowercase letters + basic punctuation)
_ASCII_TO_HID: dict[str, tuple[int, int]] = {}
for _c, _code in KEY_CODES.items():
    if len(_c) == 1 and _c.isalpha():
        _ASCII_TO_HID[_c.lower()] = (MOD_NONE, _code)
        _ASCII_TO_HID[_c.upper()] = (MOD_LSHIFT, _code)
for _digit, _code in [(str(i), KEY_CODES[str(i)]) for i in range(10)]:
    _ASCII_TO_HID[_digit] = (MOD_NONE, _code)
_ASCII_TO_HID[' '] = (MOD_NONE, KEY_CODES["SPACE"])


class BTHIDKeyboard:
    def __init__(self):
        self._ctrl_server: socket.socket | None = None
        self._intr_server: socket.socket | None = None
        self._ctrl_client: socket.socket | None = None
        self._intr_client: socket.socket | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Bluetooth setup
    # ------------------------------------------------------------------

    def _run(self, *args: str) -> None:
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("Command %s failed: %s", args, result.stderr.strip())

    def setup_bluetooth(self) -> None:
        log.info("Configuring Bluetooth adapter…")
        self._run("hciconfig", "hci0", "up")
        # Keyboard device class: Major=Peripheral(0x05), Minor=Keyboard(0x01)
        self._run("hciconfig", "hci0", "class", "0x000540")
        # piscan = discoverable + connectable
        self._run("hciconfig", "hci0", "piscan")
        self._run("hciconfig", "hci0", "name", "BT-Keyboard-RPi")
        log.info("Adapter ready. Registering HID SDP record…")
        self._register_sdp()

    def _register_sdp(self) -> None:
        import tempfile, os
        desc_hex = HID_DESCRIPTOR.hex()
        xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001">
    <sequence><uuid value="0x1124"/></sequence>
  </attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0011"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005">
    <sequence><uuid value="0x1002"/></sequence>
  </attribute>
  <attribute id="0x0009">
    <sequence>
      <sequence><uuid value="0x1124"/><uint16 value="0x0100"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d">
    <sequence>
      <sequence>
        <sequence><uuid value="0x0100"/><uint16 value="0x0013"/></sequence>
        <sequence><uuid value="0x0011"/></sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"><text value="RPi BT Keyboard"/></attribute>
  <attribute id="0x0101"><text value="Bluetooth HID Keyboard"/></attribute>
  <attribute id="0x0200"><uint16 value="0x0100"/></attribute>
  <attribute id="0x0201"><uint8 value="0x40"/></attribute>
  <attribute id="0x0202"><boolean value="false"/></attribute>
  <attribute id="0x0203"><uint8 value="0x00"/></attribute>
  <attribute id="0x0204"><boolean value="false"/></attribute>
  <attribute id="0x0205"><boolean value="false"/></attribute>
  <attribute id="0x0206">
    <sequence>
      <sequence>
        <uint8 value="0x22"/>
        <text encoding="hex" value="{desc_hex}"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207">
    <sequence>
      <sequence><uint16 value="0x0409"/><uint16 value="0x0100"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100"/></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80"/></attribute>
  <attribute id="0x020d"><boolean value="false"/></attribute>
  <attribute id="0x020e"><boolean value="false"/></attribute>
  <attribute id="0x020f"><uint16 value="0x0640"/></attribute>
  <attribute id="0x0210"><uint16 value="0x0320"/></attribute>
</record>"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write(xml)
            tmp = f.name
        try:
            self._run("sdptool", "add", "--handle=0x00010001", f"--xml={tmp}")
        finally:
            os.unlink(tmp)

    # ------------------------------------------------------------------
    # L2CAP server
    # ------------------------------------------------------------------

    def _open_l2cap_server(self, psm: int) -> socket.socket:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("00:00:00:00:00:00", psm))
        s.listen(1)
        return s

    def wait_for_connection(self) -> None:
        log.info("Opening L2CAP sockets on PSM 0x%02X and 0x%02X…", HID_CONTROL_PSM, HID_INTERRUPT_PSM)
        self._ctrl_server = self._open_l2cap_server(HID_CONTROL_PSM)
        self._intr_server = self._open_l2cap_server(HID_INTERRUPT_PSM)
        log.info("Waiting for TV to connect (pair the TV now)…")
        self._ctrl_client, ctrl_addr = self._ctrl_server.accept()
        log.info("Control channel connected from %s", ctrl_addr)
        self._intr_client, intr_addr = self._intr_server.accept()
        log.info("Interrupt channel connected from %s — HID ready!", intr_addr)

    # ------------------------------------------------------------------
    # HID report sending
    # ------------------------------------------------------------------

    def _send_intr(self, data: bytes) -> bool:
        if self._intr_client is None:
            return False
        try:
            self._intr_client.send(data)
            return True
        except OSError as e:
            log.error("Send error: %s", e)
            self._intr_client = None
            self._ctrl_client = None
            return False

    def send_key(self, modifier: int = MOD_NONE, keycodes: list[int] | None = None) -> None:
        keys = (keycodes or [])[:6]
        keys += [0] * (6 - len(keys))
        with self._lock:
            self._send_intr(bytes([0xA1, 0x01, modifier, 0x00] + keys))
            time.sleep(0.05)
            self._send_intr(bytes([0xA1, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

    def send_media(self, bits: int) -> None:
        b0 = bits & 0xFF
        b1 = (bits >> 8) & 0xFF
        with self._lock:
            self._send_intr(bytes([0xA1, 0x02, b0, b1]))
            time.sleep(0.05)
            self._send_intr(bytes([0xA1, 0x02, 0x00, 0x00]))

    def type_text(self, text: str) -> None:
        for ch in text:
            entry = _ASCII_TO_HID.get(ch)
            if entry:
                mod, code = entry
                self.send_key(mod, [code])
            time.sleep(0.02)

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    def dispatch(self, msg: dict) -> str:
        action = msg.get("action", "").lower()

        if action == "key":
            key = str(msg.get("key", "")).upper()
            code = KEY_CODES.get(key)
            if code is None:
                return f"Unknown key: {key}"
            self.send_key(MOD_NONE, [code])
            return f"key:{key}"

        if action == "media":
            key = str(msg.get("key", "")).upper()
            bits = MEDIA_CODES.get(key)
            if bits is None:
                return f"Unknown media key: {key}"
            self.send_media(bits)
            return f"media:{key}"

        if action == "combo":
            mod_name = str(msg.get("mod", "")).upper()
            key = str(msg.get("key", "")).upper()
            mod = MOD_MAP.get(mod_name, MOD_NONE)
            code = KEY_CODES.get(key)
            if code is None:
                return f"Unknown key: {key}"
            self.send_key(mod, [code])
            return f"combo:{mod_name}+{key}"

        if action == "type":
            text = str(msg.get("text", ""))
            self.type_text(text)
            return f"typed:{len(text)} chars"

        return f"Unknown action: {action}"

    @property
    def is_connected(self) -> bool:
        return self._intr_client is not None


# ------------------------------------------------------------------
# TCP command server
# ------------------------------------------------------------------

def handle_tcp_client(conn: socket.socket, addr: tuple, keyboard: BTHIDKeyboard) -> None:
    log.info("TCP client connected from %s", addr)
    try:
        buf = ""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data.decode(errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as e:
                    conn.sendall(f'{{"error": "bad json: {e}"}}\n'.encode())
                    continue
                if not keyboard.is_connected:
                    conn.sendall(b'{"error": "no BT connection"}\n')
                    continue
                result = keyboard.dispatch(msg)
                conn.sendall(json.dumps({"ok": result}).encode() + b"\n")
    except OSError:
        pass
    finally:
        conn.close()
        log.info("TCP client %s disconnected", addr)


def run_tcp_server(host: str, port: int, keyboard: BTHIDKeyboard) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    log.info("TCP command server listening on %s:%d", host, port)
    while True:
        conn, addr = srv.accept()
        t = threading.Thread(target=handle_tcp_client, args=(conn, addr, keyboard), daemon=True)
        t.start()


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Bluetooth HID keyboard server")
    parser.add_argument("--host", default="0.0.0.0", help="TCP bind address")
    parser.add_argument("--port", type=int, default=5555, help="TCP command port")
    parser.add_argument("--no-sdp", action="store_true", help="Skip SDP registration (if already done)")
    args = parser.parse_args()

    if sys.platform != "linux":
        sys.exit("This script requires Linux with BlueZ.")

    keyboard = BTHIDKeyboard()
    keyboard.setup_bluetooth()

    tcp_thread = threading.Thread(
        target=run_tcp_server,
        args=(args.host, args.port, keyboard),
        daemon=True,
    )
    tcp_thread.start()

    while True:
        try:
            keyboard.wait_for_connection()
        except OSError as e:
            log.error("L2CAP error: %s — retrying in 5 s…", e)
            time.sleep(5)
            continue

        log.info("Ready. Beelink can now send commands to %s:%d", args.host, args.port)

        # Keep alive: detect disconnect and re-listen
        while keyboard.is_connected:
            time.sleep(1)
        log.info("TV disconnected, waiting for reconnect…")


if __name__ == "__main__":
    main()
