"""The warning before an AI model download that most likely does not fit (owner, 2026-10-21)."""

from types import SimpleNamespace

from app.ai import model_fit, model_set


def test_sizes_are_read_as_lm_studio_lists_them():
    assert model_fit.size_gb("17.7 GB") == 17.7 and model_fit.size_gb("3.75 GB") == 3.75
    assert model_fit.size_gb("512 MB") == 0.5 and model_fit.size_gb("") is None and model_fit.size_gb("big") is None


def test_a_card_must_hold_the_whole_model():
    # RTX 5090 Laptop GPU (24 GB): the 27B (17.7 GB, about 21 GB loaded) fits; a 12 GB card does not
    assert model_fit.warning("17.7 GB", ram=64, card=24) == ""
    text = model_fit.warning("17.7 GB", ram=64, card=12)
    assert text.startswith("Warning") and "12 GB" in text and "about 21 GB" in text and "anyway?" in text
    assert model_fit.warning("7.2 GB", ram=16, card=12) == ""


def test_shared_memory_keeps_room_for_the_system():
    # Radxa Dragon Q8B: 16 GB shared by the system and the Adreno GPU, no card memory of its own
    assert model_fit.warning("3.75 GB", ram=15.4, card=None) == ""
    assert model_fit.warning("7.2 GB", ram=15.4, card=None) == ""
    text = model_fit.warning("17.7 GB", ram=15.4, card=None)
    assert text.startswith("Warning") and "15 GB of memory" in text and "shared" in text


def test_nothing_known_gives_no_warning():
    assert model_fit.warning("", ram=4, card=None) == ""
    assert model_fit.warning("17.7 GB", ram=None, card=None) == ""


def test_an_integrated_gpu_carve_out_is_not_a_card(monkeypatch):
    monkeypatch.setattr(model_fit, "_windows_card_gb", lambda: 0.5)
    assert model_fit.card_gb("win32") is None
    monkeypatch.setattr(model_fit, "_windows_card_gb", lambda: 24.0)
    assert model_fit.card_gb("win32") == 24.0
    run = lambda args, **k: SimpleNamespace(stdout="24463\n")
    assert model_fit._linux_card_gb(which=lambda n: "/usr/bin/nvidia-smi", run=run) == 24463 / 1024


def test_the_download_question_warns_first_and_leaves_the_choice():
    big = "llama_benchy_qwen38_27b"
    text = model_set.install_text(big, fit=lambda size: model_fit.warning(size, ram=15.4, card=None))
    assert text.startswith("Warning: this model most likely does not fit") and "lms get qwen/qwen3.8-27b" in text
    plain = model_set.install_text(big, fit=lambda size: "")
    assert plain.startswith("Download Qwen3.8 27B")
