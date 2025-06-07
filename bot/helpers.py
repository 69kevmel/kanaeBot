import logging
import discord

from . import config, database

logger = logging.getLogger(__name__)

async def safe_send_dm(user: discord.User, content: str):
    if len(content) > 2000:
        content = content[:1990] + "…"
    content = content.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    try:
        await user.send(content)
        logger.info("DM sent to %s", user)
    except discord.HTTPException as e:
        logger.warning("Failed to send DM to %s: %s", user, e)


async def get_top_scores(guild: discord.Guild, limit: int = 5):
    async with database.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, points FROM scores ORDER BY points DESC;"
            )
            all_rows = await cur.fetchall()

    top_filtered = []
    for uid, pts in all_rows:
        member = guild.get_member(int(uid))
        if member is None:
            continue
        if any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
            continue
        top_filtered.append((uid, pts))
        if len(top_filtered) >= limit:
            break
    return top_filtered


async def build_top5_message(
    bot: discord.Client,
    guild: discord.Guild,
    *,
    mention_users: bool,
    header: str,
) -> str | None:
    scores = await get_top_scores(guild, 5)
    if not scores:
        return None

    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [header]
    for idx, (uid, pts) in enumerate(scores):
        user = await bot.fetch_user(int(uid))
        name = user.mention if mention_users else user.display_name
        lines.append(f"{icons[idx]} {name} \u2192 {pts} pts")
    return "\n".join(lines)

