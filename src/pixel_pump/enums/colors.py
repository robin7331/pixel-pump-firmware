class Colors:
    NONE = (0, 0, 0)
    BLUE = (90, 183, 232)
    RED = (242, 31, 31)
    GREEN = (63, 242, 31)
    WHITE = (255, 255, 255)
    # "Remote mode": this button's active-slot action is FORWARD, so the host
    # owns it. Deliberately unlike BLUE/RED/GREEN, which mean mode, cancel and
    # vacuum.
    PURPLE = (160, 60, 230)
    # Host-assignable only: these two carry no device-side meaning and exist
    # so a host can badge a forwarded button (mapping.py's APPEARANCE_COLORS).
    AMBER = (255, 150, 20)
    CYAN = (31, 226, 226)