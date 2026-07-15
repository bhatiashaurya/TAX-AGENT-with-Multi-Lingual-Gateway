"""Unit tests for Hinglish normalization + intent extraction."""
import pytest

from services.hinglish_normalizer import extract_intent, normalize


@pytest.mark.parametrize("text,expected_contains,preserve", [
    ("मेरा GST refund status check karo", "GST refund status", "GST"),
    ("Mera income tax return kahan hai?", "Where is my income tax return", None),
    ("PAN verification chahiye", "I need PAN verification", "PAN"),
    ("मेरा TDS correction request का status batao",
     "Tell me the status of my TDS correction request", "TDS"),
])
def test_normalization_outputs(text, expected_contains, preserve):
    result = normalize(text)
    assert expected_contains.lower() in result["normalized_english"].lower()
    if preserve:
        assert preserve in result["terminology_preserved"]


def test_domain_terms_never_translated():
    result = normalize("मेरा GST aur TDS ka status")
    assert "GST" in result["normalized_english"]
    assert "TDS" in result["normalized_english"]


def test_no_duplicate_verb():
    # "status check karo": English "check" + Hindi "karo"->check must not double up.
    result = normalize("मेरा GST refund status check karo")
    assert result["normalized_english"].lower().count("check") == 1


def test_typo_lowers_confidence():
    result = normalize("GST refund sttus")
    assert result["confidence"] < 0.80  # below the quality threshold


def test_clean_query_high_confidence():
    result = normalize("मेरा GST refund status check karo")
    assert result["confidence"] >= 0.85


def test_ambiguous_flagged():
    result = normalize("TDS mera")
    assert result["ambiguous"] is True


@pytest.mark.parametrize("text,intent,tax", [
    ("Check my GST refund status", "STATUS_CHECK", "GST"),
    ("I need PAN verification", "VERIFICATION", "PAN"),
    ("Tell me the status of my TDS correction request", "CORRECTION", "TDS"),
    ("File my income tax return", "FILE_RETURN", None),
])
def test_intent_extraction(text, intent, tax):
    result = extract_intent(text, text)
    assert result["intent"] == intent
    assert result["tax_type"] == tax
