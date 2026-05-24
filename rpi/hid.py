# HID keyboard + consumer controls report descriptor
HID_DESCRIPTOR = bytes([
    # Keyboard (Report ID 1)
    0x05, 0x01,  # USAGE_PAGE (Generic Desktop)
    0x09, 0x06,  # USAGE (Keyboard)
    0xa1, 0x01,  # COLLECTION (Application)
    0x85, 0x01,  #   REPORT_ID (1)
    0x05, 0x07,  #   USAGE_PAGE (Keyboard)
    0x19, 0xe0,  #   USAGE_MINIMUM (Left Control)
    0x29, 0xe7,  #   USAGE_MAXIMUM (Right GUI)
    0x15, 0x00,  #   LOGICAL_MINIMUM (0)
    0x25, 0x01,  #   LOGICAL_MAXIMUM (1)
    0x75, 0x01,  #   REPORT_SIZE (1)
    0x95, 0x08,  #   REPORT_COUNT (8) - modifier bits
    0x81, 0x02,  #   INPUT (Data,Var,Abs)
    0x95, 0x01,  #   REPORT_COUNT (1)
    0x75, 0x08,  #   REPORT_SIZE (8) - reserved byte
    0x81, 0x03,  #   INPUT (Const,Var,Abs)
    0x95, 0x06,  #   REPORT_COUNT (6)
    0x75, 0x08,  #   REPORT_SIZE (8) - 6-key rollover
    0x15, 0x00,  #   LOGICAL_MINIMUM (0)
    0x25, 0x65,  #   LOGICAL_MAXIMUM (101)
    0x05, 0x07,  #   USAGE_PAGE (Keyboard)
    0x19, 0x00,  #   USAGE_MINIMUM (0)
    0x29, 0x65,  #   USAGE_MAXIMUM (101)
    0x81, 0x00,  #   INPUT (Data,Ary,Abs)
    0xc0,        # END_COLLECTION
    # Consumer / Media keys (Report ID 2) - 16 bits, one per key
    0x05, 0x0C,  # USAGE_PAGE (Consumer Devices)
    0x09, 0x01,  # USAGE (Consumer Control)
    0xa1, 0x01,  # COLLECTION (Application)
    0x85, 0x02,  #   REPORT_ID (2)
    0x15, 0x00,  #   LOGICAL_MINIMUM (0)
    0x25, 0x01,  #   LOGICAL_MAXIMUM (1)
    0x75, 0x01,  #   REPORT_SIZE (1)
    0x95, 0x10,  #   REPORT_COUNT (16)
    0x09, 0xB5,  #   Next Track
    0x09, 0xB6,  #   Previous Track
    0x09, 0xB7,  #   Stop
    0x09, 0xB8,  #   Eject
    0x09, 0xCD,  #   Play/Pause
    0x09, 0xE2,  #   Mute
    0x09, 0xE9,  #   Volume Up
    0x09, 0xEA,  #   Volume Down
    0x09, 0x23,  #   AC Home
    0x09, 0x94,  #   AL Local Browser
    0x09, 0x92,  #   AL Calculator
    0x09, 0x2A,  #   AC Stop
    0x09, 0x21,  #   AC Search
    0x09, 0x83,  #   AL Consumer Control Config
    0x09, 0x8A,  #   AL Email Reader
    0x09, 0x96,  #   AL Internet Browser
    0x81, 0x02,  #   INPUT (Data,Var,Abs)
    0xc0,        # END_COLLECTION
])

# USB HID Usage Table keycodes (Section 10: Keyboard/Keypad)
KEY_CODES: dict[str, int] = {
    # Navigation (most useful for TV)
    "UP":        0x52,
    "DOWN":      0x51,
    "LEFT":      0x50,
    "RIGHT":     0x4F,
    "ENTER":     0x28,
    "ESCAPE":    0x29,
    "BACKSPACE": 0x2A,
    "TAB":       0x2B,
    "SPACE":     0x2C,
    "HOME":      0x4A,
    "END":       0x4D,
    "PAGEUP":    0x4B,
    "PAGEDOWN":  0x4E,
    "DELETE":    0x4C,
    "INSERT":    0x49,
    # Function keys
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C,  "F4": 0x3D,
    "F5": 0x3E, "F6": 0x3F, "F7": 0x40,  "F8": 0x41,
    "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
    # Letters
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08,
    "F": 0x09, "G": 0x0A, "H": 0x0B, "I": 0x0C, "J": 0x0D,
    "K": 0x0E, "L": 0x0F, "M": 0x10, "N": 0x11, "O": 0x12,
    "P": 0x13, "Q": 0x14, "R": 0x15, "S": 0x16, "T": 0x17,
    "U": 0x18, "V": 0x19, "W": 0x1A, "X": 0x1B, "Y": 0x1C,
    "Z": 0x1D,
    # Numbers
    "0": 0x27, "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21,
    "5": 0x22, "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26,
}

# Modifier bitmask (byte 0 of keyboard report)
MOD_NONE   = 0x00
MOD_LCTRL  = 0x01
MOD_LSHIFT = 0x02
MOD_LALT   = 0x04
MOD_LGUI   = 0x08
MOD_RCTRL  = 0x10
MOD_RSHIFT = 0x20
MOD_RALT   = 0x40
MOD_RGUI   = 0x80

# Consumer key bit positions in Report ID 2 (16-bit field, matches descriptor order)
MEDIA_CODES: dict[str, int] = {
    "NEXT":      0x0001,
    "PREV":      0x0002,
    "STOP":      0x0004,
    "EJECT":     0x0008,
    "PLAYPAUSE": 0x0010,
    "MUTE":      0x0020,
    "VOLUP":     0x0040,
    "VOLDOWN":   0x0080,
    "MEDIA_HOME":     0x0100,
    "BROWSER":   0x0200,
    "CALC":      0x0400,
    "AC_STOP":   0x0800,
    "SEARCH":    0x1000,
    "CONFIG":    0x2000,
    "EMAIL":     0x4000,
    "INTERNET":  0x8000,
}
