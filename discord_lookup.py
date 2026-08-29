"""Look up members of the configured Discord server with the bot token.

Enrolling somebody should attach their face to their *real* Discord
account, otherwise the credits they earn at the kiosk never meet the ones
they earn on Discord. This is the shared way to ask Discord who is in the
server; the kiosk API and the web client both go through it.

Everything here degrades politely: if the token is missing, revoked, or
the guild is not configured, callers get DiscordUnavailable with a
message worth showing a human, and the UI falls back to typing a name.
"""
import requests

import config

DISCORD_API = "https://discord.com/api/v10"
TOKEN_PLACEHOLDER = "your-bot-token-here"


class DiscordUnavailable(Exception):
    """Discord cannot answer right now — the reason is worth showing."""


def token_configured() -> bool:
    return bool(config.DISCORD_TOKEN) and config.DISCORD_TOKEN != TOKEN_PLACEHOLDER


def available() -> bool:
    """True when a member list can actually be fetched."""
    return token_configured() and config.GUILD_ID != 0


def avatar_url(user: dict) -> str | None:
    user_id, avatar = user.get("id"), user.get("avatar")
    if user_id and avatar:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=128"
    return None


def _get(path: str, params: dict | None = None):
    if not token_configured():
        raise DiscordUnavailable(
            "No Discord bot token is configured on the server.")
    try:
        r = requests.get(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {config.DISCORD_TOKEN}"},
            params=params,
            timeout=10,
        )
    except requests.RequestException as e:
        raise DiscordUnavailable(f"Could not reach Discord: {e}")
    if r.status_code == 401:
        raise DiscordUnavailable(
            "Discord rejected the bot token — it has been reset or revoked.")
    if r.status_code == 403:
        raise DiscordUnavailable(
            "The bot is not allowed to list members of that server.")
    if r.status_code == 404:
        raise DiscordUnavailable("Not found on Discord.")
    if not r.ok:
        raise DiscordUnavailable(f"Discord returned {r.status_code}.")
    return r.json()


def _person(member: dict) -> dict:
    user = member.get("user", {})
    return {
        "discord_id": user.get("id"),
        "username": user.get("username"),
        "display_name": (member.get("nick") or user.get("global_name")
                         or user.get("username")),
        "avatar_url": avatar_url(user),
    }


def search_members(query: str, limit: int = 10) -> list:
    """Members of the configured guild whose name matches `query`."""
    if config.GUILD_ID == 0:
        raise DiscordUnavailable(
            "GUILD_ID is not set on the server, so the member list is unknown.")
    members = _get(f"/guilds/{config.GUILD_ID}/members/search",
                   params={"query": query, "limit": limit})
    return [_person(m) for m in members if not m.get("user", {}).get("bot")]


def list_members(limit: int = 100) -> list:
    """The first `limit` members of the guild, for an empty search box."""
    if config.GUILD_ID == 0:
        raise DiscordUnavailable(
            "GUILD_ID is not set on the server, so the member list is unknown.")
    members = _get(f"/guilds/{config.GUILD_ID}/members",
                   params={"limit": limit})
    return [_person(m) for m in members if not m.get("user", {}).get("bot")]
