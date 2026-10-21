import pytest
from pydantic import SecretStr

from app.config import Settings
from app.runs.service import groq_client_factory

URLS = {
    "database_url": "postgresql://incidentlens_app:fake-pw-0000000000@127.0.0.1:5432/incidentlens",
    "tools_ro_database_url": "postgresql://tools_ro:fake-pw-1111111111@127.0.0.1:5432/incidentlens",
}


@pytest.mark.parametrize("key", [None, "", "   ", "change-me-groq"], ids=["none", "empty", "blank", "placeholder"])
def test_investigations_disabled_without_a_real_key(key):
    settings = Settings(_env_file=None, **URLS, groq_api_key=key)

    assert groq_client_factory(settings) is None


def test_real_looking_key_enables_investigations():
    settings = Settings(_env_file=None, **URLS, groq_api_key=SecretStr("gsk_fake_but_not_placeholder"))

    assert groq_client_factory(settings) is not None
