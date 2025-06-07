import logging
import discord

from . import config

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

async def get_top_scores(pool, guild: discord.Guild, limit=5):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, points FROM scores ORDER BY points DESC;"
            )
            rows = await cur.fetchall()
    results = []
    for uid, pts in rows:
        member = guild.get_member(int(uid))
        if not member:
            continue
        if any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
            continue
        results.append((uid, pts))
        if len(results) >= limit:
            break
    return results
