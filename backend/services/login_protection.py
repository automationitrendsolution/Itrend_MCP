"""
Login protection.

Failed login attempts are counted in the cache. After too many failures the
login is refused for a cooldown period, even if the password is correct.

Two counters are kept:
- per username + IP address: stops password guessing against one account
  without letting an attacker lock a real user out from other machines;
- per IP address (higher limit): stops one machine trying many usernames.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

logger = logging.getLogger(__name__)

IP_LIMIT_MULTIPLIER = 4


def get_client_ip(request: HttpRequest) -> str:
    """
    Return the client's IP address.

    By default only REMOTE_ADDR is trusted, because X-Forwarded-For can be forged.
    Behind nginx + Docker, REMOTE_ADDR is the proxy's address for every visitor, so
    with TRUST_PROXY_HEADERS on we use the LAST X-Forwarded-For entry: the one nginx
    appended itself. Earlier entries come from the client and are ignored.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"login-failures:{digest}"


def _keys(username: str, ip: str) -> tuple[str, str]:
    return _key("user-ip", username.strip().lower(), ip), _key("ip", ip)


def is_locked_out(username: str, ip: str) -> bool:
    user_key, ip_key = _keys(username, ip)
    limit = settings.LOGIN_MAX_FAILED_ATTEMPTS
    if limit <= 0:
        return False  # lockout switched off
    return cache.get(user_key, 0) >= limit or cache.get(ip_key, 0) >= limit * IP_LIMIT_MULTIPLIER


def record_failed_login(username: str, ip: str) -> None:
    timeout = settings.LOGIN_LOCKOUT_SECONDS
    for key in _keys(username, ip):
        cache.add(key, 0, timeout)
        try:
            cache.incr(key)
        except ValueError:
            # The key expired between add() and incr().
            cache.set(key, 1, timeout)
    logger.warning("Failed login for username=%r from ip=%s", username, ip)


def reset_failed_logins(username: str, ip: str) -> None:
    cache.delete(_keys(username, ip)[0])
