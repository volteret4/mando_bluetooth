"""
BlueZ DBus pairing agent + HID SDP profile registration.

Pairing: when the TV shows a 6-digit code (Passkey Entry), BlueZ calls
RequestPasskey. We wait for the user to supply the code via console or
via the TCP {"action":"pin","code":"..."} command.

SDP: RegisterProfile adds the HID service record to BlueZ's SDP database
so the TV can discover PSM 0x11/0x13 after pairing. The actual L2CAP
connections are handled by raw sockets in bt_hid_server.py (bound first,
before the adapter becomes discoverable).
"""

import logging
import os
import threading
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

log = logging.getLogger(__name__)

AGENT_INTERFACE   = "org.bluez.Agent1"
AGENT_PATH        = "/org/mando_bluetooth/pairing_agent"
HID_PROFILE_PATH  = "/org/mando_bluetooth/hid_profile"
HID_UUID          = "00001124-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# HID SDP profile (registers SDP record only; L2CAP is handled externally)
# ---------------------------------------------------------------------------

class _HIDSdpProfile(dbus.service.Object):
    """Dummy profile object whose only job is to exist on the DBus so
    ProfileManager1.RegisterProfile can attach the ServiceRecord XML to it."""

    def __init__(self, bus):
        super().__init__(bus, HID_PROFILE_PATH)

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        log.info("HID SDP profile released")

    @dbus.service.method("org.bluez.Profile1",
                         in_signature="oha{sv}", out_signature="")
    def NewConnection(self, path, fd, properties):
        # Our raw L2CAP sockets (bound before the adapter became discoverable)
        # should receive incoming connections before BlueZ's profile manager does.
        # If this callback fires anyway, close the fd so the TV can retry the
        # raw socket path.
        log.warning("HIDSdpProfile.NewConnection called unexpectedly from %s — "
                    "closing fd so raw socket can accept", path)
        try:
            raw = fd.take() if hasattr(fd, "take") else int(fd)
            os.close(raw)
        except Exception:
            pass

    @dbus.service.method("org.bluez.Profile1",
                         in_signature="o", out_signature="")
    def RequestDisconnection(self, path):
        log.info("HID SDP profile disconnection: %s", path)


# ---------------------------------------------------------------------------
# Pairing agent
# ---------------------------------------------------------------------------

class _PairingAgent(dbus.service.Object):
    def __init__(self, bus, on_pin_request):
        super().__init__(bus, AGENT_PATH)
        self._on_pin_request = on_pin_request

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        log.info("Pairing agent released")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        log.info("Service authorized: uuid=%s device=%s", uuid, device)

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        log.info("Device authorization: %s", device)

    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print("\n" + "=" * 52)
        print(f"  EMPAREJAMIENTO Bluetooth")
        print(f"  Código en pantalla de la TV: {passkey:06d}")
        print(f"  ► Pulsa CONFIRMAR/SÍ en la TV para emparejar")
        print("=" * 52 + "\n", flush=True)
        log.info("RequestConfirmation: passkey=%06d device=%s", passkey, device)
        time.sleep(1)

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        log.info("Legacy PIN requested for %s", device)
        return str(self._on_pin_request(str(device)))

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        """SSP Passkey Entry — TV shows the code, we must return it."""
        log.info("Passkey Entry requested for %s", device)
        code = self._on_pin_request(str(device))
        return dbus.UInt32(int(code))

    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"\n[BT] Passkey: {passkey:06d}  (dígitos introducidos: {entered})")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"\n[BT] PIN Code: {pincode}")

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        log.warning("Pairing cancelled by remote device")


# ---------------------------------------------------------------------------
# Public manager
# ---------------------------------------------------------------------------

class PairingManager:
    """
    Manages the BlueZ pairing agent + HID SDP registration.

    Usage:
        pm = PairingManager()
        pm.start()                      # registers agent + GLib loop
        pm.register_hid_sdp(descriptor) # adds HID SDP record
        pm.provide_pin("123456")        # called from TCP command handler
    """

    def __init__(self):
        self._pin_event  = threading.Event()
        self._pin_value: str | None = None
        self._bus: dbus.SystemBus | None = None

    # ------------------------------------------------------------------
    # PIN supply (called from TCP handler thread)
    # ------------------------------------------------------------------

    def provide_pin(self, code: str) -> None:
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
        print("    [B]  Desde el Beelink →  pin:XXXXXX")
        print("  Tienes 60 segundos.")
        print("=" * 55, flush=True)

        console_result: list[str] = []

        def _read_console() -> None:
            try:
                val = input("  Código PIN > ").strip()
                if val:
                    console_result.append(val)
                    self._pin_event.set()
            except EOFError:
                pass

        threading.Thread(target=_read_console, daemon=True).start()

        if not self._pin_event.wait(timeout=60):
            raise dbus.exceptions.DBusException(
                "org.bluez.Error.Rejected",
                "PIN timeout: no code entered within 60 seconds",
            )

        pin = self._pin_value or (console_result[0] if console_result else None)
        if not pin:
            raise dbus.exceptions.DBusException(
                "org.bluez.Error.Rejected", "No PIN provided"
            )

        log.info("Returning PIN to BlueZ: %s", pin)
        return pin

    # ------------------------------------------------------------------
    # DBus setup
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Register pairing agent and start GLib mainloop."""
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        _PairingAgent(self._bus, self._on_pin_request)

        agent_mgr = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )
        # KeyboardOnly → always Passkey Entry (TV shows code, we return it)
        agent_mgr.RegisterAgent(AGENT_PATH, "KeyboardOnly")
        agent_mgr.RequestDefaultAgent(AGENT_PATH)
        log.info("Pairing agent registered (capability=KeyboardOnly)")

        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True, name="glib-loop").start()

    def register_hid_sdp(self, hid_descriptor: bytes) -> None:
        """Add the HID service SDP record via BlueZ ProfileManager1.

        This is called AFTER prepare_l2cap_servers() has bound PSM 0x11/0x13,
        so the kernel routes incoming L2CAP connections to our raw sockets
        rather than to this profile handler.
        """
        assert self._bus is not None, "Call start() first"

        desc_hex = hid_descriptor.hex()
        service_record = f"""<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"><sequence><uuid value="0x1124"/></sequence></attribute>
  <attribute id="0x0004">
    <sequence>
      <sequence><uuid value="0x0100"/><uint16 value="0x0011"/></sequence>
      <sequence><uuid value="0x0011"/></sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005"><sequence><uuid value="0x1002"/></sequence></attribute>
  <attribute id="0x0009">
    <sequence><sequence><uuid value="0x1124"/><uint16 value="0x0100"/></sequence></sequence>
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
    <sequence><sequence><uint16 value="0x0409"/><uint16 value="0x0100"/></sequence></sequence>
  </attribute>
  <attribute id="0x020b"><uint16 value="0x0100"/></attribute>
  <attribute id="0x020c"><uint16 value="0x0c80"/></attribute>
  <attribute id="0x020d"><boolean value="false"/></attribute>
  <attribute id="0x020e"><boolean value="false"/></attribute>
  <attribute id="0x020f"><uint16 value="0x0640"/></attribute>
  <attribute id="0x0210"><uint16 value="0x0320"/></attribute>
</record>"""

        _HIDSdpProfile(self._bus)

        profile_mgr = dbus.Interface(
            self._bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.ProfileManager1",
        )
        opts = dbus.Dictionary(
            {
                "ServiceRecord":          dbus.String(service_record),
                "RequireAuthentication":  dbus.Boolean(False),
                "RequireAuthorization":   dbus.Boolean(False),
            },
            signature="sv",
        )
        try:
            profile_mgr.RegisterProfile(HID_PROFILE_PATH, HID_UUID, opts)
            log.info("HID SDP record registered via DBus ProfileManager1")
        except dbus.exceptions.DBusException as e:
            if "Already Exists" in str(e):
                log.info("HID profile already registered")
            else:
                log.error("HID SDP registration failed: %s", e)
                raise
