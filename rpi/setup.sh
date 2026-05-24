#!/bin/bash
# One-time setup for Raspberry Pi 4 Bluetooth HID keyboard server.
# Run once as root: sudo bash setup.sh

set -e

echo "=== BT HID Setup for Raspberry Pi 4 ==="

# Dependencies
apt-get update -q
apt-get install -y python3 python3-dbus python3-gi bluez bluez-tools

# Enable BlueZ compatibility mode + disable input plugin
# --compat       → enables sdptool SDP socket
# --noplugin=input → stops BlueZ input plugin from claiming HID PSMs 0x11/0x13
#                    (those PSMs must be free for our server to bind to them)
BT_SERVICE=/lib/systemd/system/bluetooth.service
DESIRED_FLAGS="--compat --noplugin=input"

if grep -q "ExecStart=.*--noplugin=input" "$BT_SERVICE"; then
    echo "[OK] BlueZ flags already set ($DESIRED_FLAGS)"
elif grep -q "ExecStart=.*--compat" "$BT_SERVICE"; then
    # --compat present but --noplugin=input missing
    sed -i "s|ExecStart=/usr/lib/bluetooth/bluetoothd --compat|ExecStart=/usr/lib/bluetooth/bluetoothd $DESIRED_FLAGS|" "$BT_SERVICE"
    echo "[OK] Added --noplugin=input to BlueZ flags"
else
    sed -i "s|ExecStart=/usr/lib/bluetooth/bluetoothd|ExecStart=/usr/lib/bluetooth/bluetoothd $DESIRED_FLAGS|" "$BT_SERVICE"
    echo "[OK] BlueZ flags set: $DESIRED_FLAGS"
fi

# Make sdptool writable by the bluetooth group
chmod 777 /var/run/sdp 2>/dev/null || true

systemctl daemon-reload
systemctl restart bluetooth
sleep 2

# Unblock Bluetooth if rfkill has it soft- or hard-blocked
echo "Desbloqueando Bluetooth (rfkill)..."
rfkill unblock bluetooth
rfkill unblock all   # por si hay un bloqueo genérico
sleep 1

# Verify rfkill state
if rfkill list bluetooth 2>/dev/null | grep -q "Soft blocked: yes"; then
    echo "[ERROR] El Bluetooth sigue bloqueado por software."
    echo "        Comprueba 'rfkill list' y desbloquea manualmente con: rfkill unblock bluetooth"
    exit 1
fi
if rfkill list bluetooth 2>/dev/null | grep -q "Hard blocked: yes"; then
    echo "[ERROR] El Bluetooth está bloqueado por hardware (interruptor físico o firmware)."
    echo "        En la RPi esto puede indicar un problema con el firmware de la placa."
    echo "        Asegúrate de que /boot/config.txt NO tenga 'dtoverlay=disable-bt'"
    exit 1
fi

# Bring up the adapter
if ! hciconfig hci0 up 2>/dev/null; then
    echo "[ERROR] No se puede levantar hci0."
    echo "        Intenta: sudo systemctl restart bluetooth && rfkill unblock bluetooth"
    exit 1
fi

# Keyboard device class: Major=Peripheral(0x05,0x10), Minor=Keyboard(0x40)
hciconfig hci0 class 0x000540
hciconfig hci0 piscan
hciconfig hci0 name "RPi-BT-Keyboard"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Run the server:   sudo python3 bt_hid_server.py"
echo "  2. On the TV: go to Bluetooth settings and scan for devices"
echo "  3. Pair with 'RPi-BT-Keyboard'"
echo "  4. On the Beelink: run   python3 client.py <rpi-ip>"
echo ""
echo "To find the RPi IP address: hostname -I"
