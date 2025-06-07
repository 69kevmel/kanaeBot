import asyncio
import logging
from datetime import datetime, date, timezone
import os
import random
import feedparser

import discord
from discord.ext import tasks

from . import config, database, helpers, state

logger = logging.getLogger(__name__)

@tasks.loop(minutes=1)
async def weekly_recap(bot: discord.Client):
    now = datetime.now(timezone.utc)
    today = now.date()
    if now.hour == 15 and now.minute == 0:
        last_date = await database.get_last_recap_date(database.db_pool)
        if last_date is None:
            return
        if (today - last_date).days < 2 or await database.has_sent_recap(database.db_pool, today):
            return
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            return
        message = await helpers.build_top5_message(
            bot,
            channel.guild,
            mention_users=True,
            header="🌟 Hall of Flamme — TOP 5 Kanaé 🌟",
        )
        if not message:
            return
        message += (
            "\n\nRespect à vous les frérots, vous envoyez du très lourd ! Continuez comme ça, le trône du **Kanaé d’Or ** vous attend ! 🛋️🌈"
            "\n\n🌿 Restez chill, partagez la vibe. Kanaé représente ! 🌿"
        )
        await channel.send(message)
        await database.mark_recap_sent(database.db_pool, today)
        logger.info("Weekly recap sent")

@tasks.loop(minutes=1)
async def daily_scores_backup(bot: discord.Client):
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute == 0:
        channel = bot.get_channel(config.MOD_LOG_CHANNEL_ID)
        if not channel:
            return
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT user_id, points FROM scores;")
                rows = await cur.fetchall()
        filename = "scores_backup.txt"
        with open(filename, "w") as f:
            for user_id, points in rows:
                f.write(f"{user_id},{points}\n")
        try:
            await channel.send("🗂️ **Voici le fichier des scores mis à jour :**", file=discord.File(filename))
            logger.info("Score backup uploaded")
        except Exception as e:
            logger.warning("Failed to send score backup: %s", e)
        finally:
            os.remove(filename)

@tasks.loop(minutes=5)
async def update_voice_points(bot: discord.Client):
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                user_id = str(member.id)
                state.voice_times[user_id] = state.voice_times.get(user_id, 0) + 300
                if state.voice_times[user_id] >= 1800:
                    new_total = await database.add_points(database.db_pool, user_id, 1)
                    state.voice_times[user_id] -= 1800
                    if new_total in [10, 50, 100]:
                        await helpers.safe_send_dm(member, f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")

async def fetch_and_send_news(bot: discord.Client):
    while database.db_pool is None:
        await asyncio.sleep(1)
    await bot.wait_until_ready()
    channel = bot.get_channel(config.NEWS_CHANNEL_ID)
    if not channel:
        logger.warning("News channel not found")
        return
    logger.info("News channel found: %s", channel)
    while True:
        now = datetime.now(timezone.utc)
        today = now.date()
        logger.info("Checking news feeds")
        all_entries = []
        for feed_url in config.RSS_FEEDS:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = entry.get('published_parsed')
                if not published:
                    continue
                entry_date = date(published.tm_year, published.tm_mon, published.tm_mday)
                if entry_date != today:
                    continue
                link = entry.link
                if not await database.has_sent_news(database.db_pool, link):
                    all_entries.append(entry)
        if all_entries:
            entry = random.choice(all_entries)
            link = entry.link
            published_date = date(
                entry.published_parsed.tm_year,
                entry.published_parsed.tm_mon,
                entry.published_parsed.tm_mday,
            )
            message = (
                f"🌿 **Nouvelles fraîches de la journée !** 🌿\n"
                f"**{entry.title}**\n"
                f"{link}\n\n"
                f"🗓️ Publié le : {published_date}"
            )
            await channel.send(message)
            logger.info("News posted: %s", entry.title)
            await database.mark_news_sent(database.db_pool, link, today)
        else:
            logger.info("No news to post")
        await asyncio.sleep(3 * 3600)

