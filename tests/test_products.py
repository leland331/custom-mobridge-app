"""The exe's static product table, loaded as data (re/analysis/PRODUCT_TABLE.md)."""
from mobridge import products


def test_table_has_every_record_in_exe_order():
    assert len(products.PRODUCTS) == 77
    assert products.PRODUCTS[0][:3] == (0x001c, 0x1008, 'Bavsound')
    assert products.PRODUCTS[20] == (0x202c, 0x180cf, 'DA-G2.Pro A2B Ford', '12 Channel Professional DSP')
    assert products.PRODUCTS[-1][:2] == (0xfacc, 0x10443)


def test_lookup_follows_the_exe_map():
    assert products.flags(0x202c) == 0x180cf
    assert products.name(0x4006) == 'Bizzarrini A2B'     # duplicate id: last record wins
    assert products.flags(0x4002) == 0                   # Morgan: flags zeroed in the initialiser
    assert products.flags(0x1234) == 0 and products.name(0x1234) is None


def test_request_filter_matches_fun_1400ac190():
    from mobridge.products import request_filter, DSPREAD_MASK
    assert request_filter(DSPREAD_MASK, 0xffffffff) == DSPREAD_MASK
    # 0x202c: bits 0-3, 6, 7, 15, 16 -> loses only 0x070f sub 0/1 (bit8) and head unit (bit10)
    assert request_filter(DSPREAD_MASK, 0x180cf) == DSPREAD_MASK & ~0x3 & ~0x10
    assert request_filter(DSPREAD_MASK, 0) == 0x1c1f0008     # Morgan: only the unfiltered bits


def test_connected_button_texts_are_the_exes():
    from mobridge import uirules as R
    assert R.connect_button_text(0) == 'Offline Mode  '
    assert R.connect_button_text(2) == 'Connecting  '
    assert R.connect_button_text(3) == 'Connected  '
    assert R.connect_button_text(3, update_available=True) == 'Firmware Update Available  '
    assert R.connect_button_text(14) == 'Disconnected  '
    assert R.connect_button_text(16) == 'Incompatible Device  '
