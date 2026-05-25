"""
BlueZ DBus pairing agent for keyboard SSP (Passkey Entry).

When the TV shows a 6-digit code and says "type it on the keyboard",
BlueZ calls RequestPasskey on this agent. We wait for the user to
supply the code (via console or via the TCP {"action":"pin","code":"..."} command)
and return it to BlueZ, which completes the Diffie-Hellman key exchange.
"""

import logging
import threading
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

log = logging.getLogger(__name__)

AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/org/mando_bluetooth/pairing_agent"


class _PairingAgent(dbus.service.Object):
    def __init__(self, bus, on_pin_request):
        super().__init__(bus, AGENT_PATH)
        self._on_pin_request = on_pin_request

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        log.info("Agent released")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        log.info("Service authorized: uuid=%s device=%s", uuid, device)

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        log.info("Device authorization: %s", device)

    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        # SSP Numeric Comparison: TV shows this code and asks user to confirm.
        # The user must press "Yes/Confirm" on the TV. We auto-confirm on RPi side.
        print("\n" + "=" * 52)
        print(f"  EMPAREJAMIENTO Bluetooth")
        print(f"  Código en pantalla de la TV: {passkey:06d}")
        print(f"  ► Pulsa CONFIRMAR/SÍ en la TV para emparejar")
        print("=" * 52 + "\n", flush=True)
        log.info("RequestConfirmation: passkey=%06d device=%s", passkey, device)
        # Small delay so the user sees the message before the pairing dialog closes
        time.sleep(1)

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        """Legacy PIN pairing (pre-SSP TVs)."""
        log.info("Legacy PIN requested for %s", device)
        return str(self._on_pin_request(str(device)))

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        """SSP Passkey Entry — TV shows the code, we must return it."""
        log.info("Passkey requested for %s", device)
        code = self._on_pin_request(str(device))
        return dbus.UInt32(int(code))

    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"\n[BT] Passkey on screen: {passkey:06d}  (digits entered: {entered})")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"\n[BT] PIN Code: {pincode}")

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        log.warning("Pairing cancelled by remote device")


class PairingManager:
    """
    Manages the BlueZ pairing agent and PIN handoff between threads.

    Usage:
        pm = PairingManager()
        pm.start()                  # registers agent, starts GLib loop
        pm.provide_pin("123456")    # called from TCP command handler
    """

    def __init__(self):
        self._pin_event = threading.Event()
        self._pin_value: str | None = None

    def provide_pin(self, code: str) -> None:
        """Supply the PIN seen on the TV screen (from TCP or console)."""
        self._pin_value = code.strip()
        self._pin_event.set()

    def _on_pin_request(self, device_path: str) -> str:
        self._pin_event.clear()
        self._pin_value = None

        print("\n" + "=" * 55)
        print("  EMPAREJAMIENTO BLUETOOTH — Passkey Entry")
        print("  La TV muestra un código de 6 dígitos.")
        print("  Introdúcelo usando UNA de estas opciones:")
        print("    [A]  Aquí mismo → escríbelo y pulsa Enter")
        print('    [B]  Desde el Beelink →  pin:XXXXXX')
        print("  Tienes 60 segundos.")
        print("=" * 55, flush=True)

        # Thread for console input (non-blocking)
        console_result: list[str] = []

        def read_console() -> None:
            try:
                val = input("  Código PIN > ").strip()
                if val:
                    console_result.append(val)
                    self._pin_event.set()
            except EOFError:
                pass

        t = threading.Thread(target=read_console, daemon=True)
        t.start()

        # Wait up to 60 s for PIN from either source
        if not self._pin_event.wait(timeout=60):
            raise dbus.exceptions.DBusException(
                "org.bluez.Error.Rejected",
                "PIN timeout: no code entered within 60 seconds",
            )

        # TCP takes priority; fall back to console
        pin = self._pin_value or (console_result[0] if console_result else None)
        if not pin:
            raise dbus.exceptions.DBusException(
                "org.bluez.Error.Rejected", "No PIN provided"
            )

        log.info("Returning PIN to BlueZ: %s", pin)
        return pin

    def start(self) -> None:
        """Register agent and start GLib mainloop in a daemon thread."""
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        _PairingAgent(bus, self._on_pin_request)

        manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )
        # KeyboardOnly → siempre Passkey Entry: TV muestra el código, nosotros lo devolvemos.
        # Evita Numeric Comparison (que requiere confirmar en la TV y puede confundir).
        manager.RegisterAgent(AGENT_PATH, "KeyboardOnly")
        manager.RequestDefaultAgent(AGENT_PATH)
        log.info("Pairing agent registered with capability=KeyboardOnly")

        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True, name="glib-loop").start()
