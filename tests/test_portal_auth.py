import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
pytestmark = pytest.mark.django_db


def test_first_request_creates_user_keyed_by_discord_id(client, portal_headers):
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 200
    user = User.objects.get(discord_id='1001')
    assert user.username == '1001'
    assert user.email == 'kira@example.com'
    assert user.avatar_url == 'https://cdn.example.com/a.png'
    assert not user.has_usable_password()
    assert user.profile.interface_language == 'en'


def test_second_request_reuses_user_and_updates_email(client, portal_headers):
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_EMAIL'] = 'new@example.com'
    client.get('/library/', **portal_headers)
    assert User.objects.count() == 1
    assert User.objects.get().email == 'new@example.com'


def test_admin_header_makes_staff_in_group_but_never_superuser(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    client.get('/library/', **portal_headers)
    user = User.objects.get(discord_id='1001')
    assert user.is_staff
    assert not user.is_superuser
    assert user.groups.filter(name='app-admins').exists()


def test_demotion_removes_staff_and_group(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'member'
    client.get('/library/', **portal_headers)
    user = User.objects.get(discord_id='1001')
    assert not user.is_staff
    assert not user.groups.filter(name='app-admins').exists()


def test_unknown_access_level_is_treated_as_member(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'superuser'
    client.get('/library/', **portal_headers)
    assert not User.objects.get().is_staff


def test_missing_header_is_rejected(client):
    assert client.get('/library/').status_code == 401


def test_headers_from_untrusted_peer_are_rejected(client, portal_headers):
    response = client.get('/library/', REMOTE_ADDR='8.8.8.8', **portal_headers)
    assert response.status_code == 403
    assert not User.objects.exists()


def test_session_does_not_survive_header_removal(client, portal_headers):
    client.get('/library/', **portal_headers)
    assert client.get('/library/').status_code == 401


def test_changing_the_header_switches_user_not_session(client, portal_headers):
    client.get('/library/', **portal_headers)
    portal_headers['HTTP_X_DISCORD_USER_ID'] = '2002'
    client.get('/library/', **portal_headers)
    assert User.objects.filter(discord_id__in=['1001', '2002']).count() == 2
    response = client.get('/library/', **portal_headers)
    assert response.wsgi_request.user.discord_id == '2002'


def test_admin_group_is_created_by_migration():
    assert Group.objects.filter(name='app-admins').exists()


@pytest.mark.parametrize('bad_id', ['', 'admin', '12 34', '١٢٣', '1' * 33])
def test_malformed_user_id_is_rejected_without_creating_a_user(
    client, portal_headers, bad_id
):
    portal_headers['HTTP_X_DISCORD_USER_ID'] = bad_id
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 400
    assert not User.objects.exists()


def test_overlong_email_and_avatar_are_ignored_not_fatal(client, portal_headers):
    portal_headers['HTTP_X_DISCORD_EMAIL'] = 'a' * 300 + '@example.com'
    portal_headers['HTTP_X_DISCORD_AVATAR_URL'] = 'https://x.example/' + 'a' * 300
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 200
    user = User.objects.get(discord_id='1001')
    assert user.email == ''
    assert user.avatar_url == ''


def test_missing_optional_headers_leave_existing_values(client, portal_headers):
    client.get('/library/', **portal_headers)
    del portal_headers['HTTP_X_DISCORD_EMAIL']
    del portal_headers['HTTP_X_DISCORD_AVATAR_URL']
    client.get('/library/', **portal_headers)
    assert User.objects.get().email == 'kira@example.com'


def test_portal_id_does_not_adopt_an_existing_local_account(client, portal_headers):
    local = User.objects.create_user(username='123456789012345678', password='x')
    local.is_superuser = True
    local.save()
    portal_headers['HTTP_X_DISCORD_USER_ID'] = '123456789012345678'
    portal_headers['HTTP_X_DISCORD_ACCESS_LEVEL'] = 'admin'
    response = client.get('/library/', **portal_headers)
    assert response.status_code == 401
    local.refresh_from_db()
    assert local.discord_id is None
    assert not local.is_staff
    assert not local.groups.filter(name='app-admins').exists()


def test_x_forwarded_for_is_not_honoured(client, portal_headers):
    response = client.get(
        '/library/',
        REMOTE_ADDR='8.8.8.8',
        HTTP_X_FORWARDED_FOR='127.0.0.1',
        **portal_headers,
    )
    assert response.status_code == 403
    assert not User.objects.exists()
