"""Speaker map/labelling matches the official app (word-for-word) and seeds
the reference current assignment."""
from mobridge import channel_map as cm


def test_full_vocabulary_present():
    objs = {s.object_name for s in cm.SPEAKERS}
    for expected in ('frontlefttweeter', 'frontleftmid', 'frontleftwoofer',
                     'FrontLeftFullRange', 'centermid', 'centertweet',
                     'leftsubwoofer', 'rightsubwoofer', 'headlinerlefttweet',
                     'headrestrighttweet', 'TOSLINK Left', 'TOSLINK Right'):
        assert expected in objs


def test_labels_are_app_wording():
    assert cm.BY_OBJECT['frontlefttweeter'].display == 'Front Left Tweeter'
    assert cm.BY_OBJECT['FrontLeftFullRange'].display == 'Front Left Full Range'
    assert cm.BY_OBJECT['frontrightwoofer'].display == 'Front Right Woofer'
    assert cm.output_channel_label(11) == 'Output Channel 11'


def test_reference_current_assignment_seeded():
    assigned = dict(cm.CURRENT_ASSIGNMENT)
    assert assigned == {'frontleftwoofer': 11, 'frontrightwoofer': 12,
                        'rearleftwoofer': 1, 'rearrightwoofer': 2,
                        'centermid': 3, 'rightsubwoofer': 4}
    # every assignment key is a real position
    assert all(o in cm.BY_OBJECT for o in cm.CURRENT_ASSIGNMENT)


def test_assignment_table_marks_unassigned():
    table = dict(cm.assignment_table())
    assert table['Front Left Woofer'] == 'Output Channel 11'
    assert table['Front Left Tweeter'] == cm.UNASSIGNED
