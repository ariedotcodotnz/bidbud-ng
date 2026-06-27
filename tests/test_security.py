from app import security


def test_round_trip():
    token = security.encrypt("hunter2")
    assert token != "hunter2"
    assert security.decrypt(token) == "hunter2"


def test_empty_values():
    assert security.encrypt("") == ""
    assert security.decrypt("") == ""


def test_invalid_token_returns_empty():
    assert security.decrypt("not-a-valid-fernet-token") == ""


def test_tokens_are_non_deterministic():
    # Fernet embeds a timestamp + IV, so encrypting twice differs.
    assert security.encrypt("abc") != security.encrypt("abc")
    assert security.decrypt(security.encrypt("abc")) == "abc"
