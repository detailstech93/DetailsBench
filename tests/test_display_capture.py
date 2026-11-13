from app.simulation.display_capture import _guid, mostly_black


def test_a_black_screenshot_is_told_from_a_menu_and_a_title_bar_does_not_hide_it():
    # NucBox K10, 2026-11-13: RDR2's window covered the screen, its title bar white, the rest black
    w, h = 192, 108
    black = bytearray(w * h * 4)
    black[:w * 2 * 4] = b"\xf0" * (w * 2 * 4)             # a two-row white title bar
    assert mostly_black(bytes(black))
    menu = bytearray(black)
    for i in range(0, len(menu), 4 * 7):                  # words and a picture
        menu[i:i + 3] = b"\x80\x80\x80"
    assert not mostly_black(bytes(menu)) and mostly_black(b"")


def test_guids_are_laid_out_as_windows_reads_them():
    g = _guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")
    assert (g.a, g.b, g.c, bytes(g.d)) == (0x6F15AAF2, 0xD208, 0x4E89, bytes.fromhex("9ab4489535d34f9c"))
