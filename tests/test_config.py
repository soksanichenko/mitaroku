import ipaddress

import pytest
from mitaroku.config import Settings
from pydantic import ValidationError


def test_yaml_values_are_loaded(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(
        'secret_key: from-yaml\n'
        'debug: true\n'
        'database:\n  password: pw\n  host: db.internal\n'
    )
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    for name in ('MITAROKU_SECRET_KEY', 'MITAROKU_DATABASE__PASSWORD'):
        monkeypatch.delenv(name)
    settings = Settings()
    assert settings.secret_key.get_secret_value() == 'from-yaml'
    assert settings.debug is True
    assert settings.database.host == 'db.internal'


def test_environment_overrides_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text(
        'secret_key: from-yaml\ndatabase:\n  password: pw\n  host: yaml-host\n'
    )
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    monkeypatch.setenv('MITAROKU_SECRET_KEY', 'from-env')
    monkeypatch.setenv('MITAROKU_DATABASE__HOST', 'env-host')
    settings = Settings()
    assert settings.secret_key.get_secret_value() == 'from-env'
    assert settings.database.host == 'env-host'


def test_missing_required_value_is_reported(monkeypatch):
    monkeypatch.delenv('MITAROKU_SECRET_KEY')
    with pytest.raises(ValidationError) as error:
        Settings()
    assert 'secret_key' in str(error.value)


def test_invalid_trusted_proxy_is_rejected(monkeypatch):
    monkeypatch.setenv('MITAROKU_AUTH__TRUSTED_PROXIES', '["not-a-network"]')
    with pytest.raises(ValidationError):
        Settings()


def test_unknown_key_is_rejected(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.yaml'
    config_file.write_text('secret_keyy: typo\n')
    monkeypatch.setenv('MITAROKU_CONFIG', str(config_file))
    with pytest.raises(ValidationError):
        Settings()


def test_default_trusted_proxies_are_loopback_only():
    from accounts.portal import is_trusted_peer

    networks = [ipaddress.ip_network(item) for item in Settings().auth.trusted_proxies]
    assert is_trusted_peer('127.0.0.1', networks)
    assert is_trusted_peer('::1', networks)
    assert not is_trusted_peer('172.18.0.5', networks)


def test_provider_defaults_need_no_key():
    providers = Settings().providers
    assert providers.tmdb_token is None
    assert providers.anilist_min_interval == 2.0
    assert providers.fribb_url.endswith('/anime-list-full.json')


def test_provider_token_comes_from_the_environment_and_stays_secret(monkeypatch):
    monkeypatch.setenv('MITAROKU_PROVIDERS__TMDB_TOKEN', 'tmdb-secret')
    providers = Settings().providers
    assert providers.tmdb_token.get_secret_value() == 'tmdb-secret'
    assert 'tmdb-secret' not in repr(providers)


@pytest.mark.parametrize('value', ['', '   '])
def test_an_empty_provider_token_counts_as_not_configured(monkeypatch, value):
    monkeypatch.setenv('MITAROKU_PROVIDERS__TMDB_TOKEN', value)
    assert Settings().providers.tmdb_token is None


def test_an_empty_token_degrades_the_default_clients(monkeypatch):
    from catalog.sync.common import default_clients
    from mitaroku.config import load_settings

    monkeypatch.setenv('MITAROKU_PROVIDERS__TMDB_TOKEN', '')
    load_settings.cache_clear()
    try:
        assert default_clients().tmdb is None
    finally:
        load_settings.cache_clear()


def test_the_email_block_is_off_by_default():
    settings = Settings()
    assert settings.email.host == ''
    assert settings.email.port == 587
    assert settings.email.starttls is True
    assert settings.email.ssl is False
    assert settings.email.password is None
    assert settings.public_url == ''


def test_a_host_without_a_from_address_is_rejected(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__HOST', 'smtp.example')
    with pytest.raises(ValidationError):
        Settings()


def test_a_host_with_a_from_address_is_accepted(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__HOST', 'smtp.example')
    monkeypatch.setenv('MITAROKU_EMAIL__FROM_ADDRESS', 'mitaroku@example.com')
    assert Settings().email.host == 'smtp.example'


def test_the_ssl_flag_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__SSL', 'true')
    assert Settings().email.ssl is True


def test_ssl_with_the_default_starttls_is_accepted(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__SSL', 'true')
    monkeypatch.setenv('MITAROKU_EMAIL__PORT', '465')
    email = Settings().email
    assert email.ssl is True
    assert email.starttls is True


def test_ssl_with_an_explicit_starttls_is_rejected(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__SSL', 'true')
    monkeypatch.setenv('MITAROKU_EMAIL__STARTTLS', 'true')
    with pytest.raises(ValidationError, match='ssl'):
        Settings()


def test_ssl_with_starttls_explicitly_off_is_accepted(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__SSL', 'true')
    monkeypatch.setenv('MITAROKU_EMAIL__STARTTLS', 'false')
    assert Settings().email.starttls is False


def test_public_url_loses_its_trailing_slash_and_must_be_http(monkeypatch):
    monkeypatch.setenv('MITAROKU_PUBLIC_URL', 'https://m.example/')
    assert Settings().public_url == 'https://m.example'
    monkeypatch.setenv('MITAROKU_PUBLIC_URL', 'ftp://x')
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv('MITAROKU_PUBLIC_URL', '')
    assert Settings().public_url == ''


@pytest.mark.parametrize('value', ['', '   '])
def test_a_blank_email_password_is_none(monkeypatch, value):
    monkeypatch.setenv('MITAROKU_EMAIL__PASSWORD', value)
    assert Settings().email.password is None


def test_the_email_password_is_not_in_the_repr(monkeypatch):
    monkeypatch.setenv('MITAROKU_EMAIL__PASSWORD', 'smtp-secret')
    email = Settings().email
    assert email.password.get_secret_value() == 'smtp-secret'
    assert 'smtp-secret' not in repr(email)
