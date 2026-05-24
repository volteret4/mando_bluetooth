#!/bin/bash
# One-time setup for Raspberry Pi 4 Bluetooth HID keyboard server.
# Run once as root: sudo bash setup.sh

set -e

echo "=== BT HID Setup for Raspberry Pi 4 ==="

# Dependencies
apt-get update -q
apt-get install -y python3 python3-dbus python3-gi bluez bluez-tools

# Enable BlueZ compatibility mode (needed for sdptool)
BT_SERVICE=/lib/systemd/system/bluetooth.service
if grep -q "ExecStart=.*--compat" "$BT_SERVICE"; then
    echo "[OK] BlueZ already in compat mode"
else
    sed -i 's|ExecStart=/usr/lib/bluetooth/bluetoothd|ExecStart=/usr/lib/bluetooth/bluetoothd --compat|' "$BT_SERVICE"
    echo "[OK] BlueZ compat mode enabled"
fi

# Make sdptool writable by the bluetooth group
chmod 777 /var/run/sdp 2>/dev/null || true

systemctl daemon-reload
systemctl restart bluetooth
sleep 2

# Keyboard device class: Major=Peripheral(0x05,0x10), Minor=Keyboard(0x40)
hciconfig hci0 up
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
