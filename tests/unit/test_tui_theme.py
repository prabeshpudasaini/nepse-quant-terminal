from apps.tui import theme


def test_vol_formats_millions_thousands_and_units():
    assert theme._vol(1_500_000) == "1.50M"
    assert theme._vol(1500) == "2K"
    assert theme._vol(999) == "999"


def test_pct_text_and_style():
    up = theme._pct(3.0)
    assert up.plain == "+3.00%"
    assert up.style == theme.GAIN_HI

    down = theme._pct(-3.0)
    assert down.plain == "-3.00%"
    assert down.style == theme.LOSS_HI

    bold_up = theme._pct(3.0, bold=True)
    assert bold_up.style == f"bold {theme.GAIN_HI}"


def test_npr_text_and_style():
    big = theme._npr(2_000_000)
    assert big.plain == "+NPR 2.00M"
    assert big.style == f"bold {theme.GAIN_HI}"

    small = theme._npr(-500)
    assert small.plain == "NPR -500"
    assert small.style == f"bold {theme.LOSS_HI}"


def test_color_constants_are_importable_strings():
    for name in (
        "AMBER", "WHITE", "DIM", "LABEL", "GAIN_HI", "GAIN",
        "LOSS_HI", "LOSS", "CYAN", "YELLOW", "PURPLE", "BLUE",
    ):
        value = getattr(theme, name)
        assert isinstance(value, str)
        assert value.startswith("#")
