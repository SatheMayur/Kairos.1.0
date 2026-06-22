"""Tests for candidate name hygiene (WhatsApp handles vs real names)."""
from app.utils.names import (
    clean_name, is_placeholder_name, friendly_display_name, extract_name_from_text,
)


def test_handles_are_placeholders():
    assert is_placeholder_name("@lok😊😊..", "7383401662")
    assert is_placeholder_name("mr_gk_borse", "8469736036")
    assert is_placeholder_name("😊😊", "9123456780")
    assert is_placeholder_name("9313135768", "9313135768")   # the phone itself
    assert is_placeholder_name("", None)
    assert is_placeholder_name("WhatsApp Lead")


def test_real_names_pass():
    assert not is_placeholder_name("Saradhara pritkumar Rajeshbhai", "9313135768")
    assert not is_placeholder_name("Rahul", "9000000000")     # single capitalized first name
    assert not is_placeholder_name("Priya Sharma")


def test_friendly_display():
    assert friendly_display_name("@lok😊😊..", "7383401662") == "lok"
    assert friendly_display_name("😊😊", "9123456780") == "WhatsApp user ••6780"
    assert friendly_display_name("9313135768", "9313135768") == "WhatsApp user ••5768"
    assert friendly_display_name("Priya Sharma", "9") == "Priya Sharma"


def test_extract_name_from_text():
    assert extract_name_from_text("hi my name is Rahul Shah") == "Rahul Shah"
    assert extract_name_from_text("I am Priya") == "Priya"
    assert extract_name_from_text("this is amit kumar patel") == "Amit Kumar Patel"
    assert extract_name_from_text("2") is None
    assert extract_name_from_text("Hi") is None


def test_clean_name_strips_junk():
    assert clean_name("@lok😊") == "lok"
    assert clean_name("mr_gk_borse") == "mr gk borse"
    assert clean_name("😊😊") == ""
