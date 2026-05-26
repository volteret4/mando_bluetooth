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
    {"action": "pin",   "code": "123456"}         -> supply pairing PIN
"""

import argparse
import json
import logging
import socket
import subprocess
import sys
import threading
import time

import dbus

from pairing_agent import PairingManager
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
    def __init__(self, pairing_manager: PairingManager):
        self._pairing = pairing_manager
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

    def _get_adapter_path(self) -> str:
        """Return the DBus path of the first available Bluetooth adapter."""
        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        for path, ifaces in manager.GetManagedObjects().items():
            if "org.bluez.Adapter1" in ifaces:
                return str(path)
        raise RuntimeError("No Bluetooth adapter found — is bluetooth running?")

    def setup_bluetooth(self, skip_sdp: bool = False) -> None:
        adapter_path = self._get_adapter_path()
        log.info("Configuring adapter %s via DBus…", adapter_path)

        bus  = dbus.SystemBus()
        props = dbus.Interface(
            bus.get_object("org.bluez", adapter_path),
            "org.freedesktop.DBus.Properties",
        )

        props.Set("org.bluez.Adapter1", "Powered",             dbus.Boolean(True))
        props.Set("org.bluez.Adapter1", "Alias",               dbus.String("BT-Keyboard"))
        # DiscoverableTimeout=0 → stays discoverable indefinitely
        props.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(0))
        props.Set("org.bluez.Adapter1", "Discoverable",        dbus.Boolean(True))
        # PairableTimeout=0 → stays pairable indefinitely
        props.Set("org.bluez.Adapter1", "PairableTimeout",     dbus.UInt32(0))
        props.Set("org.bluez.Adapter1", "Pairable",            dbus.Boolean(True))

        # Keyboard device class is not exposed via Adapter1 — use hciconfig.
        # After setting the class, hciconfig may clear ISCAN, so we force
        # piscan (page+inquiry scan) explicitly to ensure TV can discover us.
        hci = adapter_path.split("/")[-1]   # e.g. /org/bluez/hci0 → hci0
        self._run("hciconfig", hci, "class", "0x000540")
        self._run("hciconfig", hci, "piscan")

        # Log current HCI state so we can verify ISCAN/PSCAN are set
        result = subprocess.run(["hciconfig", hci], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            log.info("hciconfig: %s", line)

        if skip_sdp:
            log.info("Adapter ready (SDP skipped — use --no-sdp only for discovery tests)")
        else:
            log.info("Adapter ready: discoverable=True, pairable=True, class=0x000540")
            self._register_sdp()

    def _register_sdp(self) -> None:
        # Use DBus ProfileManager1 — more reliable than sdptool for HID on modern BlueZ.
        # The raw L2CAP sockets were bound in prepare_l2cap_servers() before this call,
        # so the kernel routes incoming connections to our sockets, not to this profile handler.
        self._pairing.register_hid_sdp(HID_DESCRIPTOR)

    # ------------------------------------------------------------------
    # L2CAP server
    # ------------------------------------------------------------------

    def _open_l2cap_server(self, psm: int) -> socket.socket:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("00:00:00:00:00:00", psm))
        except OSError as e:
            raise OSError(
                f"Cannot bind L2CAP PSM 0x{psm:02X}: {e}\n"
                "  → Check that bluetoothd runs with --noplugin=input\n"
                "    sudo systemctl cat bluetooth | grep ExecStart"
            ) from e
        s.listen(1)
        return s

    def prepare_l2cap_servers(self) -> None:
        """Open server sockets BEFORE making adapter discoverable to avoid race conditions."""
        log.info("Opening L2CAP server sockets on PSM 0x%02X and 0x%02X…",
                 HID_CONTROL_PSM, HID_INTERRUPT_PSM)
        self._ctrl_server = self._open_l2cap_server(HID_CONTROL_PSM)
        self._intr_server = self._open_l2cap_server(HID_INTERRUPT_PSM)
        log.info("L2CAP sockets ready — waiting for TV to connect")

    def accept_hid_connection(self) -> None:
        """Block until the TV connects both HID channels."""
        assert self._ctrl_server and self._intr_server, "Call prepare_l2cap_servers() first"
        self._ctrl_client, ctrl_addr = self._ctrl_server.accept()
        log.info("Control channel  (PSM 0x11) connected from %s", ctrl_addr)
        self._intr_client, intr_addr = self._intr_server.accept()
        log.info("Interrupt channel (PSM 0x13) connected from %s — HID ready!", intr_addr)

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

        if action == "pin":
            code = str(msg.get("code", "")).strip()
            if not code.isdigit() or len(code) != 6:
                return "PIN must be exactly 6 digits"
            self._pairing.provide_pin(code)
            return f"pin:{code} sent to pairing agent"

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
                # pin action is allowed even before HID connection (pairing phase)
                if not keyboard.is_connected and msg.get("action") != "pin":
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
    parser.add_argument("--no-sdp", action="store_true",
                        help="Skip RegisterProfile/SDP (useful for testing TV discovery)")
    args = parser.parse_args()

    if sys.platform != "linux":
        sys.exit("This script requires Linux with BlueZ.")

    pairing = PairingManager()
    pairing.start()

    keyboard = BTHIDKeyboard(pairing)

    # 1. Open L2CAP sockets FIRST — before the adapter becomes discoverable.
    #    This prevents the race where the TV auto-connects before we're listening.
    try:
        keyboard.prepare_l2cap_servers()
    except OSError as e:
        sys.exit(str(e))

    # 2. Configure adapter and (optionally) register SDP
    keyboard.setup_bluetooth(skip_sdp=args.no_sdp)

    # 3. TCP command server (Beelink uses this to send keys AND the pairing pin)
    tcp_thread = threading.Thread(
        target=run_tcp_server,
        args=(args.host, args.port, keyboard),
        daemon=True,
    )
    tcp_thread.start()

    log.info("=" * 55)
    log.info("Servidor listo.")
    log.info("  Si la TV pide un código de 6 dígitos:")
    log.info("  → desde el Beelink escribe:  pin:XXXXXX")
    log.info("  → o escribe el código aquí y pulsa Enter")
    log.info("=" * 55)

    while True:
        try:
            keyboard.accept_hid_connection()
        except OSError as e:
            log.error("L2CAP error: %s — retrying in 5 s…", e)
            time.sleep(5)
            continue

        log.info("HID activo. Beelink puede enviar comandos a %s:%d", args.host, args.port)

        # Keep alive: detect disconnect and re-listen
        while keyboard.is_connected:
            time.sleep(1)
        log.info("TV disconnected, waiting for reconnect…")


if __name__ == "__main__":
    main()
