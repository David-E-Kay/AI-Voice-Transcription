from dictate_core import clean_text


def test_clean_text_strips_and_collapses():
    assert clean_text("  hello   world  ") == "hello world"


def test_clean_text_empty_is_empty():
    assert clean_text("   ") == ""


def test_clean_text_collapses_newlines():
    assert clean_text("line one\n  line two") == "line one line two"


import numpy as np
from dictate_core import frames_to_audio, is_too_short


def test_frames_to_audio_concatenates_and_flattens():
    a = np.ones((4, 1), dtype=np.float32)
    b = np.zeros((2, 1), dtype=np.float32)
    out = frames_to_audio([a, b])
    assert out.shape == (6,)
    assert out.dtype == np.float32


def test_frames_to_audio_empty_returns_empty():
    assert frames_to_audio([]).shape == (0,)


def test_is_too_short_true_for_quick_tap():
    audio = np.zeros(int(16000 * 0.1), dtype=np.float32)  # 100 ms
    assert is_too_short(audio) is True


def test_is_too_short_false_for_real_clip():
    audio = np.zeros(16000, dtype=np.float32)  # 1 s
    assert is_too_short(audio) is False


from dictate_core import backdrop_box


def test_backdrop_box_pads_15pct_x_and_10pct_y():
    box_w, box_h, off_x, off_y = backdrop_box(200, 100)
    assert (off_x, off_y) == (30, 10)          # 15% of 200, 10% of 100
    assert (box_w, box_h) == (260, 120)        # +2*offset each dim


def test_backdrop_box_offsets_are_symmetric():
    box_w, box_h, off_x, off_y = backdrop_box(137, 100)
    assert box_w == 137 + 2 * off_x
    assert box_h == 100 + 2 * off_y
