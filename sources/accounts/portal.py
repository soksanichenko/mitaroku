"""Helpers for the SSO portal headers."""

import ipaddress

from accounts.models import User
from django.contrib.auth.models import Group

ADMIN_GROUP = 'app-admins'
EMAIL_MAX = 254
AVATAR_MAX = 200


def is_trusted_peer(
    remote_addr: str,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Say whether a peer address belongs to a trusted proxy network.

    Args:
        remote_addr: The ``REMOTE_ADDR`` of the request.
        networks: Trusted proxy networks.

    Returns:
        True when the address parses and lies in one of the networks.
    """
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(address in network for network in networks)


def is_valid_discord_id(value: str) -> bool:
    """Say whether a header value looks like a Discord snowflake id.

    Discord ids are decimal digits. Rejecting anything else keeps portal ids
    from colliding with local usernames such as ``admin``.

    Args:
        value: Value of ``X-Discord-User-Id``.

    Returns:
        True for 1 to 32 ASCII digits.
    """
    return value.isascii() and value.isdigit() and len(value) <= 32


def sync_user(user: User, *, email: str, avatar_url: str, access_level: str) -> None:
    """Copy portal-provided attributes onto the user.

    The role follows the portal on every request: ``admin`` makes the user
    staff and a member of ``app-admins`` (never a superuser); anything else
    removes both.

    Args:
        user: The authenticated user.
        email: Value of ``X-Discord-Email``, possibly empty.
        avatar_url: Value of ``X-Discord-Avatar-Url``, possibly empty.
        access_level: Value of ``X-Discord-Access-Level``.
    """
    is_admin = access_level == 'admin'
    changed: list[str] = []
    # Values longer than the columns are ignored rather than crashing the request.
    if email and len(email) <= EMAIL_MAX and user.email != email:
        user.email = email
        changed.append('email')
    if avatar_url and len(avatar_url) <= AVATAR_MAX and user.avatar_url != avatar_url:
        user.avatar_url = avatar_url
        changed.append('avatar_url')
    if user.is_staff != is_admin:
        user.is_staff = is_admin
        changed.append('is_staff')
    if changed:
        user.save(update_fields=changed)
    group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
    in_group = user.groups.filter(pk=group.pk).exists()
    if is_admin and not in_group:
        user.groups.add(group)
    elif not is_admin and in_group:
        user.groups.remove(group)
