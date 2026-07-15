"""Unit tests for script-aware language detection."""
from services.language_detector import detect


def test_pure_devanagari_is_hindi():
    d = detect("मेरा रिफंड कहाँ है")
    assert d["code"] == "hi"
    assert d["script"] == "Devanagari"
    assert d["confidence"] > 0.8


def test_mixed_script_flags_hinglish():
    d = detect("मेरा GST refund status check karo")
    assert d["code"] == "hi"
    assert d["is_hinglish"] is True
    assert d["confidence"] >= 0.95  # both scripts present => strong signal


def test_romanized_hindi_is_hinglish():
    d = detect("Mera income tax return kahan hai")
    assert d["code"] == "hi"
    assert d["is_hinglish"] is True


def test_plain_english_not_hinglish():
    d = detect("Where is my refund")
    assert d["code"] == "en"
    assert d["is_hinglish"] is False


def test_tamil_detected():
    d = detect("என் வருமான வரி")
    assert d["code"] == "ta"


def test_empty_text_is_safe():
    d = detect("!!! 123 ???")
    assert d["code"] == "en"
    assert d["is_hinglish"] is False
