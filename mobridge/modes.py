"""Device modes and FDP event ids, as recovered from the app decompile (NOTES.md 11b)."""
from enum import IntEnum


class DeviceMode(IntEnum):
    DISCONNECTED = 0
    BOOTS = 1    # bootloader: text console, `flash -uart` + ZMODEM recovery path
    WIZARD = 2   # setup wizard
    MDI = 3      # normal operation, framed binary MDI jobs
    UNKNOWN = 4  # the RX demux switch table has a 5th slot (0-4)
    LW = 5       # transport mode 4 (single-char token console, TRANSPORT_REGION.md §1.5);
                 # the exe's UI text table (FUN_140163040) has no entry for it


class FdpEvent(IntEnum):
    SWITCHTO = 0x10000000
    SWITCHFROM = 0x10000001
    NEWJOB = 0x10000002
    CANCELJOB = 0x10000003
    RAWDATA = 0x10000004
    BOOTS_RESPONSE = 0x10000005
    LW_MESSAGE = 0x10000006      # inferred from ordering, not read directly
    WIZARD_MESSAGE = 0x10000007  # inferred from ordering, not read directly
    MDI_MESSAGE = 0x10000008
