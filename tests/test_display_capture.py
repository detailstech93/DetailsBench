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


def test_only_the_window_in_front_is_left_to_read():
    # GEEKOM, 2026-11-18: RDR2 in a 1280x720 window; the app's Stop button and an Edge report beside it were read
    from app.simulation.desktop import mask_outside
    w, h = 4, 3
    picture = bytes(range(1, w * h * 4 + 1))
    out = mask_outside(picture, w, h, (1, 1, 3, 2))       # the window: x 1-2, row 1
    for y in range(h):
        for x in range(w):
            px = out[(y * w + x) * 4:(y * w + x + 1) * 4]
            inside = 1 <= x < 3 and y == 1
            assert px == (picture[(y * w + x) * 4:(y * w + x + 1) * 4] if inside else b"\0\0\0\0")
    # a window over the whole screen (or larger, its hidden borders), or none: the picture as it is
    assert mask_outside(picture, w, h, (-8, -8, w + 8, h + 8)) == picture
    assert mask_outside(picture, w, h, None) == picture
    assert mask_outside(picture, w, h, (5, 5, 6, 6)) == picture        # off the screen: nothing to keep
