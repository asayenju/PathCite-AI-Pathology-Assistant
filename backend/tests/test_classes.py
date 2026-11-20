from app.classes import CLASS_NAMES


def test_nine_unique_classes():
    assert len(CLASS_NAMES) == 9
    assert len(set(CLASS_NAMES)) == 9


def test_verified_order():
    assert CLASS_NAMES == (
        "ADI", "BACK", "DEB", "LYM", "MUC", "MUS", "NORM", "STR", "TUM",
    )
