"""Enriched error reporting: bot-logs gets the frame, DMs get the answer.

When a command or a component callback fails, the user is never left
with a frozen interaction: the failure is reported where it can be
acted on (developer mandate, kingdoms-services#113):

- **guild interactions** — a crash report rides the guild's
  🤖-bot-logs channel: the exception, the git reference of the
  emitting line (``path:line`` resolved against the deployed commit)
  and the interaction context (who, when, where, what);
- **DM interactions** — there is no guild channel to log to; the error
  is answered in the DM itself, same rendering of the exception, with
  the interaction context.

The git reference resolves through the deployed commit sha when the
pipeline provides it (KINGDOMS_DEPLOY_* plumbing): the GitHub source
URL of the deepest frame inside ``src/kingdoms``. Without a sha the
plain ``path:line`` stands — still actionable, never a blocker.
"""

from __future__ import annotations

import datetime
import inspect
import logging
from pathlib import Path
from typing import Any

import discord

from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.discord.deploy_render import SERVICES_REPO_URL, sha7_of

logger = logging.getLogger("kingdoms.bot.errors")

_SRC_ROOT = "src/kingdoms"
_MAX_FRAMES = 20


def _repo_line(frame: inspect.FrameInfo) -> str | None:
    """Render a frame as a repo-relative path:line, None outside the repo."""
    filename = Path(frame.filename)
    parts = filename.parts
    if _SRC_ROOT in parts:
        rel = Path(*parts[parts.index(_SRC_ROOT) + 1 :])
        return f"{_SRC_ROOT}/{rel.as_posix()}:{frame.lineno}"
    return None


def emitting_frame(exc: BaseException) -> inspect.FrameInfo | None:
    """Find the deepest kingdoms frame of the exception's traceback."""
    tb = exc.__traceback__
    if tb is None:
        return None
    frames = inspect.getinnerframes(tb)
    for frame in reversed(frames):
        if _repo_line(frame) is not None:
            return frame
    return None


def git_ref(frame: inspect.FrameInfo | None, tree_url: str) -> str:
    """Render the git reference of the emitting line (URL when the sha is known).

    The pinned tree URL ends with the deployed commit sha; the GitHub
    source URL pins the exact line. Without a sha the plain repo path
    stands — the image digest can still be correlated by hand.
    """
    line = _repo_line(frame) if frame is not None else None
    if line is None:
        return ""
    sha = sha7_of(tree_url)
    if sha:
        return f"{SERVICES_REPO_URL}/blob/{sha}/{line.removeprefix(f'{_SRC_ROOT}/')}"
    return line


def _format_traceback(exc: BaseException, limit: int = _MAX_FRAMES) -> str:
    """Render a compact traceback, kingdoms frames first-class."""
    import traceback

    lines: list[str] = []
    tb = traceback.extract_tb(exc.__traceback__)
    for entry in tb[-limit:]:
        marker = "→" if _SRC_ROOT in entry.filename else " "
        lines.append(f"{marker} {entry.filename}:{entry.lineno} in {entry.name}")
    return "\n".join(lines)


def _timestamp_now() -> str:
    """ISO-8601 UTC timestamp of the failure (log line contract)."""
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def interaction_context(interaction: discord.Interaction) -> str:
    """Render the interaction context: who, when, where, what.

    DMs render explicitly (no guild to mention) — the same lines ride
    the DM answer so the user can paste them in a bug report.
    """
    who = getattr(interaction.user, "id", "?")
    where = f"guild {interaction.guild_id}" if interaction.guild_id is not None else "DM"
    what = getattr(interaction, "command", None)
    command_name = f"/{get_qualified_name(what)}" if what is not None else "component"
    when = _timestamp_now()
    return (
        f"who: <@{who}> (`{who}`)\n"
        f"when: {when}\n"
        f"where: {where}\n"
        f"what: {command_name}"
    )


def get_qualified_name(command: Any) -> str:
    """Read the qualified name of an app command (groups ride the name)."""
    parts: list[str] = []
    node: Any = command
    while node is not None:
        name = getattr(node, "name", None)
        if name:
            parts.append(str(name))
        node = getattr(node, "parent", None)
    return "/".join(reversed(parts))


def _log_content(exc: BaseException, ref: str, context: str) -> str:
    """Render the crash report content (bot-logs and DM share it)."""
    lines = [f"💥 **{type(exc).__name__}**: {exc}", "", f"```{_format_traceback(exc)}```"]
    if ref:
        lines.extend(["", f"📍 Source: {ref}"])
    if context:
        lines.extend(["", context])
    return "\n".join(lines)


async def report_interaction_error(
    interaction: discord.Interaction,
    exc: BaseException,
    tree_url: str,
    logs_service: LogService | None,
) -> None:
    """Report an interaction failure to bot-logs, or to the DM itself.

    Guild interactions: best-effort delivery to the guild's bot-logs
    channel (the LogService's resolve/provision path). DM interactions:
    answered in the DM — there is no guild channel to log to, the user
    in front of us gets the error. The answer is best-effort too: a
    failing error path never masks the original failure.
    """
    frame = emitting_frame(exc)
    ref = git_ref(frame, tree_url)
    context = interaction_context(interaction)
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    if guild_id and logs_service is not None:
        event = LifecycleEvent(
            kind="crash",
            message=_log_content(exc, ref, context),
        )
        await logs_service.log_event(guild_id, event)
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send(_log_content(exc, ref, context), ephemeral=True)
        else:
            await interaction.response.send_message(_log_content(exc, ref, context), ephemeral=True)
    except Exception:
        logger.warning("DM ERROR ANSWER FAILED — best-effort", exc_info=exc)


async def report_guild_error(
    exc: BaseException,
    guild_ids: list[str],
    tree_url: str,
    logs_service: LogService | None,
) -> None:
    """Report a non-interaction failure (gateway events) to each guild's bot-logs."""
    if logs_service is None:
        return
    for guild_id in guild_ids:
        event = LifecycleEvent(
            kind="crash",
            message=_log_content(exc, ref=git_ref(emitting_frame(exc), tree_url), context=""),
        )
        await logs_service.log_event(guild_id, event)
