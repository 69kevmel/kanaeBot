import logging
import discord

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

