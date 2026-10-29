"""The official app's static product table (mObridge DSP.exe v25.8.0, initialiser
FUN_140001390; re/analysis/PRODUCT_TABLE.md, product_table.tsv). 77 records in table
order: (product id, capability flags, name, description). The exe builds a map id ->
record from it, so for a duplicated id the LAST record wins (0x4006). Record 0 is the
template (name "Bavsound", no description); record 57 (Morgan Amplifier) has its flags
zeroed by the initialiser.

The flags word is what FUN_1400977c0 returns for the connected product. Which UI
feature each bit drives is in uirules (CAP_*)."""

PRODUCTS = (
    (0x001c, 0x1008, 'Bavsound', ''),
    (0x0005, 0x1208, 'mObridge DA1 DSP Processor', '2 Channel TOSLINK'),
    (0x0011, 0x208, 'mObridge DA2 DSP Processor', '8 Channel Standard DSP'),
    (0x0017, 0x1024f, 'mObridge DA3 DSP Processor', '8 Channel Professional DSP'),
    (0x0015, 0x8, 'mObridge Aston Martin DA1 DSP Processor', '2 Channel TOSLINK'),
    (0x001a, 0x8, 'mObridge Aston Martin DA2 DSP Processor', '8 Channel Standard DSP'),
    (0x0019, 0x1024f, 'mObridge Aston Martin DA3 DSP Processor', '8 Channel Professional DSP'),
    (0x8000, 0x8, 'NAV-TV ZEN-H Entry DSP Processor', '2 Channel TOSLINK'),
    (0x8001, 0x8, 'NAV-TV ZEN-H Standard DSP Processor', '12 Channel Standard DSP'),
    (0x8002, 0x100cf, 'NAV-TV ZEN-H DSP Processor', '12 Channel Professional DSP'),
    (0x2002, 0x180cf, 'DA-G2.Pro MOST25', '12 Channel Professional DSP'),
    (0x2024, 0x8, 'DA-G2.Standard MOST25', '12 Channel Standard DSP'),
    (0x202e, 0x8, 'DA-G2.Entry MOST25', '2 Channel TOSLINK'),
    (0x2019, 0x180cf, 'DA-G2.Pro MOST150 Mercedes', '12 Channel Professional DSP'),
    (0x2025, 0x8, 'DA-G2.Standard MOST150 Mercedes', '12 Channel Standard DSP'),
    (0x2029, 0x8, 'DA-G2.Entry MOST150 Mercedes', '2 Channel TOSLINK'),
    (0x2034, 0x180cf, 'DA-G2.Pro MOST150 cPHY Mercedes', '12 Channel Professional DSP'),
    (0x2035, 0x8, 'DA-G2.Standard MOST150 cPHY Mercedes', '12 Channel Standard DSP'),
    (0x2036, 0x8, 'DA-G2.Entry MOST150 cPHY Mercedes', '2 Channel TOSLINK'),
    (0x2031, 0x180cf, 'DA-G2.Pro A2B Generic', '12 Channel Professional DSP'),
    (0x202c, 0x180cf, 'DA-G2.Pro A2B Ford', '12 Channel Professional DSP'),
    (0x202b, 0x8, 'DA-G2.Standard A2B Ford', '12 Channel Standard DSP'),
    (0x202a, 0x8, 'DA-G2.Entry A2B Ford', '2 Channel TOSLINK'),
    (0x2044, 0x8, 'DA-G2.Entry A2B GM', '2 Channel TOSLINK'),
    (0x2045, 0x8, 'DA-G2.Standard A2B GM', '12 Channel Standard DSP'),
    (0x2046, 0x180cf, 'DA-G2.Pro A2B GM', '12 Channel Professional DSP'),
    (0x201d, 0x180c7, 'mObridge DA-G2 UNICENS150', '12 Channel Professional DSP'),
    (0x2040, 0x180cf, 'DA-G2.Pro A2B Harley Davidson', '12 Channel Professional DSP'),
    (0x2041, 0x8, 'DA-G2.Standard A2B Harley Davidson', '12 Channel Standard DSP'),
    (0x2042, 0x8, 'DA-G2.Entry A2B Harley Davidson', '2 Channel TOSLINK'),
    (0x5000, 0x180cf, 'mObridge DA-G3 MOST25', ''),
    (0x5001, 0x180cf, 'mObridge DA-G3 MOST150 cPHY Mercedes', ''),
    (0x5002, 0x180cf, 'mObridge DA-G3 AVB Ethernet GM', ''),
    (0x5003, 0x180cf, 'mObridge DA-G3 A2B Ford', '18 Channel DSP'),
    (0x0104, 0x10047, 'mObridge Analog DSP', '8 Channel DSP'),
    (0x2000, 0x1004f, 'Karma K1.x MOST25', '6.1 Channel DSP & Amplifier'),
    (0x2001, 0x102cf, 'Generic K1.x MOST25', '6.1 Channel DSP & Amplifier'),
    (0x2011, 0x10047, 'MHCP K1.x UNICENS50', '6.1 Channel DSP & Amplifier'),
    (0x2003, 0x1004f, 'OAP K1.x Analog', '6.1 Channel Amplifier'),
    (0x2013, 0x180cf, 'mObridge K1.x Analog', '6.1 Channel DSP & Amplifier'),
    (0x2015, 0x10047, 'MHCP K1.x UNICENS150', '6.1 Channel DSP & Amplifier'),
    (0x2016, 0x10047, 'Karma K1.x A2B', '6.1 Channel DSP & Amplifier'),
    (0x2017, 0x10047, 'Generic K1.x A2B', '6.1 Channel DSP & Amplifier'),
    (0x2021, 0x1004f, 'Karma K2 MOST25', '8 Channel DSP & Amplifier'),
    (0x2022, 0x10003, 'Karma K2 A2B', '8 Channel DSP & Amplifier'),
    (0x2027, 0x10147, 'Generic ANC K2 A2B', '8 Channel ANC DSP & Amplifier'),
    (0x2030, 0x10047, 'Generic K2 A2B', '8 Channel DSP & Amplifier'),
    (0x2023, 0x1804f, 'Lotus K2 A2B', '6.1 Channel DSP & Amplifier'),
    (0x202f, 0x1804f, 'Lotus K1.x A2B', '6.1 Channel DSP & Amplifier'),
    (0x2018, 0x1804f, 'Czinger K1.x A2B', '6.1 Channel DSP & Amplifier'),
    (0xd005, 0x182cf, 'mObridge K2 MOST25 Universal', '8.1.1 Channel DSP & Amplifier'),
    (0x2033, 0x182cf, 'mObridge K2 MOST150 Mercedes', '8.1.1 Channel DSP & Amplifier'),
    (0x2037, 0x182cf, 'mObridge K2 MOST150 cPHY Mercedes', '8.1.1 Channel DSP & Amplifier'),
    (0x2032, 0x180cf, 'mObridge K2 A2B Ford', '8.1.1 Channel DSP & Amplifier'),
    (0x2043, 0x180cf, 'mObridge K2 A2B Ford (Morel)', '8.1.1 Channel DSP & Amplifier'),
    (0x2038, 0x180cf, 'mObridge K2 Analog CAN Ford', '8.1.1 Channel DSP & Amplifier'),
    (0x2039, 0x180cf, 'mObridge K2 Analog Universal', '8.1.1 Channel DSP & Amplifier'),
    (0x4002, 0x0, 'Morgan Amplifier', '8 Channel Bluetooth Amplifier'),
    (0x4004, 0x180cf, 'mObridge K3 A2B Ford', '16 Channel DSP & Amplifier'),
    (0x4005, 0x180cf, 'mObridge K3 AVB Ethernet BMW', '16 Channel DSP & Amplifier'),
    (0x4006, 0x180cf, 'mObridge K3 AVB Ethernet GM', '16 Channel DSP & Amplifier'),
    (0x4006, 0x180cf, 'Bizzarrini A2B', '8 Channel DSP & Amplifier'),
    (0xd002, 0x10047, 'Prototype High Power A2B Amp', '6 Channel DSP and Amp 2x100w 2x75w 2x38w'),
    (0x201c, 0x10447, 'DA-G2 Master Node A2B (8CH)', 'TOSLINK/2CH A2B Input + 8CH A2B Output'),
    (0xf00d, 0x400, 'DA-G2 Master Node A2B (16CH)', 'TOSLINK/2CH A2B Input + 16CH A2B Output'),
    (0xf00e, 0x30400, 'DA-G2 Master Node A2B (12CH)', 'TOSLINK/2CH A2B Input + 12CH A2B Output'),
    (0x4000, 0x400, 'Dante A2B Bridge', '32x32 Dante <--> 32x32 A2B'),
    (0x4001, 0x10447, 'A2B Master (SC584)', '16CH A2B Master'),
    (0x4003, 0x400, 'A2B Master EOLT-G2 (SC584)', '16CH A2B Master EOL Tester'),
    (0xf005, 0x10443, 'DA-G2 Master Node MOST25', 'TOSLINK/MOST Input + (8 Channel Analog)/MOST Output'),
    (0xf006, 0x10443, 'DA-G2 Master Node MOST50', 'TOSLINK/MOST Input + (8 Channel Analog)/MOST Output'),
    (0xf007, 0x10443, 'DA-G2 Master Node MOST150', 'TOSLINK/MOST Input + (8 Channel Analog)/MOST Output'),
    (0xf008, 0x10443, 'DA-G2 Master Node PPAP Tester A2B', 'Programmable Noise Master Node'),
    (0xf009, 0x10443, 'DA-G2 Master Node PPAP Tester MOST25', 'Programmable Noise Master Node'),
    (0xf00a, 0x10443, 'DA-G2.Masternode INICnet', 'INICnet Masternode'),
    (0xf00b, 0x10443, 'DA-G2 Master Node MOST150 cPHY', 'TOSLINK/MOST Input + (8 Channel Analog)/MOST Output'),
    (0xfacc, 0x10443, 'DA-G2 Master Node PPAP Tester A2B CAN', 'Programmable Noise Master Node'),
)


BY_ID = {}
for _pid, _flags, _name, _desc in PRODUCTS:
    BY_ID[_pid] = (_flags, _name, _desc)


def flags(product_id):
    """Capability flags for a product id; 0 for an unknown id (the exe finds no record)."""
    rec = BY_ID.get(product_id)
    return rec[0] if rec else 0


def name(product_id):
    rec = BY_ID.get(product_id)
    return rec[1] if rec else None


def description(product_id):
    rec = BY_ID.get(product_id)
    return rec[2] if rec else None


# Request-mask bits are dspwrite.REQ_* (re/analysis/UI_REGION_2.md §1.3-1.6).
DSPREAD_MASK = 0xff1f001f    # what FUN_1400aeb00 asks for at connect


def request_filter(mask: int, product_flags: int) -> int:
    """FUN_1400ac190: AND a request mask down by the product's flag word."""
    m = mask
    if not product_flags & 0x2:
        m &= 0x7f7fffff
    if not product_flags & 0x1:
        m &= 0xdfdfffff
    if not product_flags >> 16 & 1:
        m &= 0xbfbfffff
    if not product_flags >> 8 & 1:
        m &= 0xfffffffc
    if not product_flags & 0x80:
        m &= 0xfffffffb
    if not product_flags >> 10 & 1:
        m &= 0xffffffef
    if not product_flags & 0x8:
        m &= 0xfcffffff
    return m


# Channel labels and product image per product (re/analysis/product_channel_labels.tsv,
# FUN_140097870 / the record's +0x186238 label array). id -> (image path, labels).
CHANNELS = {
    0x1c: ('', ('TOSLINK Left', 'TOSLINK Right')),
    0x5: (':/images/da.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x11: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x17: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x15: (':/images/da.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x1a: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x19: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x8000: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x8001: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x8002: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2002: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2024: (':/images/dag2.most25.standard.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x202e: ('12 Channel Standard DSP', ('TOSLINK Left', 'TOSLINK Right')),
    0x2019: (':/images/dag2.most150.ophy.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2025: (':/images/dag2.most150.ophy.standard.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2029: (':/images/dag2.most150.ophy.entry.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x2034: (':/images/dag2.most150.cphy.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2035: (':/images/dag2.most150.cphy.standard.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2036: (':/images/dag2.most150.cphy.entry.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x2031: (':/images/dag2.a2b.ford.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x202c: (':/images/dag2.a2b.ford.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x202b: (':/images/dag2.a2b.ford.standard.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x202a: (':/images/dag2.a2b.ford.entry.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x2044: (':/images/dag2.a2b.ford.entry.png', ('TOSLINK Left', 'TOSLINK Right')),
    0x2045: (':/images/dag2.a2b.ford.standard.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2046: (':/images/dag2.a2b.ford.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x201d: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2040: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2041: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'RCA 1', 'RCA 2', 'TOSLINK Left', 'TOSLINK Right')),
    0x2042: ('', ('TOSLINK Left', 'TOSLINK Right')),
    0x5000: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'TOSLINK Left', 'TOSLINK Right')),
    0x5001: (':/images/dag2.most150.cphy.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'TOSLINK Left', 'TOSLINK Right')),
    0x5002: (':/images/k2.a2b.ford.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'TOSLINK Left', 'TOSLINK Right')),
    0x5003: (':/images/dag2.a2b.ford.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'TOSLINK Left', 'TOSLINK Right')),
    0x104: (':/images/da.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2000: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2001: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2011: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2003: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2013: ('', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2015: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2016: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2017: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2021: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2022: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2027: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2030: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2023: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x202f: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x2018: (':/images/karma_amp.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xd005: (':/images/k2.most25.png', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2033: (':/images/k2.most150.png', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2037: (':/images/k2.most150.png', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2032: (':/images/k2.a2b.ford.png', ('Output Channel (Sub)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2043: (':/images/k2.a2b.ford.png', ('Output Channel (Sub)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2038: (':/images/k2.a2b.ford.png', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x2039: (':/images/k2.a2b.ford.png', ('Output Channel (Sub)', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel (Hi-Power)', 'Output Channel (Hi-Power)', 'Output Channel', 'Output Channel', 'Pre-out')),
    0x4002: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x4004: (':/images/k2.a2b.ford.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x4005: (':/images/k2.a2b.ford.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x4006: (':/images/k2.a2b.ford.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0x4006: ('', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xd002: (':/images/karma_amp.png', ('Output Channel (Sub)', 'Output Channel (75w)', 'Output Channel (unused)', 'Output Channel (75w)', 'Output Channel (unused)', 'Output Channel (38w)', 'Output Channel (38w)', 'Output Channel (Sub)')),
    0x201c: (':/images/dag2.most25.pro.png', ('A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B')),
    0xf00d: (':/images/dag2.most25.pro.png', ('A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B')),
    0xf00e: (':/images/dag2.most25.pro.png', ('A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B')),
    0x4000: ('', ()),
    0x4001: ('', ('A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B')),
    0x4003: ('', ('A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B', 'A2B')),
    0xf005: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xf006: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xf007: (':/images/dag2.most25.pro.png', ('Stereo0-L', 'Stereo0-R', 'Mono 1', 'Mono 2', 'Mono 3', 'Mono 4', 'Stereo1-L', 'Stereo1-R')),
    0xf008: (':/images/dag2.most25.pro.png', ('A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel')),
    0xf009: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xf00a: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xf00b: (':/images/dag2.most25.pro.png', ('Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel', 'Output Channel')),
    0xfacc: (':/images/dag2.most25.pro.png', ('A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel', 'A2B Channel')),
}


def channel_labels(pid) -> tuple:
    return CHANNELS.get(pid, (None, ()))[1]


def channel_count(pid) -> int:
    """dev+0x48: the length of the product's label array (0 = unknown product)."""
    return len(channel_labels(pid))


def image_path(pid) -> str | None:
    return CHANNELS.get(pid, (None, ()))[0] or None


# Vehicle Type row text per product (runtime product map, model+0x1d8 -> +0x10).
# The map is built at runtime by FUN_140001390, so only values seen on the official
# app are carried: 0x202c from the reference connected video (2026-10-21).
VEHICLE = {0x202c: 'Ford A2B G1'}
