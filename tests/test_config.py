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
