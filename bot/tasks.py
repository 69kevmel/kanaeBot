import asyncio
import logging
from datetime import datetime, date, timezone
import os
import random
import feedparser
import socket

import discord
from discord.ext import tasks

from . import config, database, helpers, state

logger = logging.getLogger(__name__)

@tasks.loop(minutes=1)
async def weekly_recap(bot: discord.Client):
    now = datetime.now(timezone.utc)
    if now.hour == 15 and now.minute == 0 and now.date().toordinal() % 2 == 0:
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            return
        guild = channel.guild
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, points FROM scores ORDER BY points DESC;"
                )
                all_rows = await cur.fetchall()
        top_filtered = []
        for uid, pts in all_rows:
            member = guild.get_member(int(uid))
            if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
                continue
            top_filtered.append((uid, pts))
            if len(top_filtered) >= 5:
                break
        if not top_filtered:
            return

        places = [
            "🥇 **1ʳᵉ place : {name} — {pts} pts 🔥👑**",
            "🥈 **2ᵉ place : {name} — {pts} pts 💨🎖️**",
            "🥉 **3ᵉ place : {name} — {pts} pts 🌿🥉**",
            "🏅 4ᵉ place : {name} — {pts} pts ✨",
            "🏅 5ᵉ place : {name} — {pts} pts ✨",
        ]

        lines = ["🌟 TOP 5 pour le concours du **Kanaé d'or** 🌟", ""]

        for i, (user_id, points) in enumerate(top_filtered, 1):
            user = await bot.fetch_user(int(user_id))
            lines.append(places[i - 1].format(name=user.display_name, pts=points))
            if i == 3:
                lines.append("")

        for i in range(len(top_filtered) + 1, 6):
            lines.append(places[i - 1].format(name="-", pts="-"))
            if i == 3:
                lines.append("")

        lines.append("")
        lines.append(
            "Respect à vous les frérots, vous envoyez du très lourd ! Continuez comme ça, le trône du **Kanaé d’Or ** vous attend ! 🛋️🌈"
        )
        lines.append("")
        lines.append("🌿 Restez chill, partagez la vibe. Kanaé représente ! 🌿")

        msg = "\n".join(lines)
        await channel.send(msg)
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
                    new_total = await database.add_points(database.db_pool, user_id, 5)
                    state.voice_times[user_id] -= 1800
                    if new_total in [10, 50, 100]:
                        await helpers.safe_send_dm(member, f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")

@tasks.loop(hours=2)
async def fetch_and_send_news(bot: discord.Client):
    logger.info("🚀 Tâche fetch_and_send_news démarrée (cycle de 2 heures)")  # AJOUT ICI
    await bot.wait_until_ready()

    while database.db_pool is None:
        await asyncio.sleep(1)

    channel = bot.get_channel(config.NEWS_CHANNEL_ID)
    if not channel:
        logger.warning("❗ Canal de news introuvable.")
        return

    logger.info("🔍 Récupération des flux RSS...")
    today = date.today()
    socket.setdefaulttimeout(10)  # Timeout global pour les flux

    all_entries = []

    for feed_url in config.RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                logger.warning("⚠️ Flux corrompu : %s → %s", feed_url, feed.bozo_exception)
                continue

            for entry in feed.entries:
                published = entry.get('published_parsed')
                if not published:
                    continue

                entry_date = date(published.tm_year, published.tm_mon, published.tm_mday)
                if entry_date != today:
                    continue

                if hasattr(entry, 'link') and isinstance(entry.link, str):
                    link = entry.link
                elif hasattr(entry, 'links') and entry.links and isinstance(entry.links[0], dict):
                    link = entry.links[0].get('href', '❓ lien inconnu')
                else:
                    link = '❓ lien inconnu'

                if not await database.has_sent_news(database.db_pool, link):
                    all_entries.append((entry, link))

        except Exception as e:
            logger.error("❌ Erreur sur le flux %s : %s", feed_url, e)
            continue

    if not all_entries:
        logger.info("📭 Aucun nouvel article à publier aujourd’hui.")
        return

    for entry, link in all_entries:
        title = entry.title
        published_date = date(
            entry.published_parsed.tm_year,
            entry.published_parsed.tm_mon,
            entry.published_parsed.tm_mday
        )

        message = (
            f"🌿 **Nouvelles fraîches de la journée !** 🌿\n"
            f"**{title}**\n"
            f"{link}\n\n"
            f"🗓️ Publié le : {published_date}"
        )

        await channel.send(message)
        await database.mark_news_sent(database.db_pool, link, today)
        await asyncio.sleep(2)  # anti-spam pour Discord

    logger.info("✅ %d news postées", len(all_entries))
logger.info("🌀 Tâche fetch_and_send_news terminée.")


async def spawn_pokeweed_loop(bot: discord.Client):
    await bot.wait_until_ready()
    logger.info("🌱 Boucle de spawn Pokéweed démarrée !")

    while True:
        # Délai entre 4h et 5h
        delay = random.randint(14400, 18000)
        logger.info(f"⏳ Prochain spawn Pokéweed dans {delay // 60} minutes.")
        
        try:
            await asyncio.sleep(delay)
            # PROTECTION ANTI-CRASH ICI :
            try:
                await spawn_pokeweed(bot)
            except Exception as e:
                logger.error(f"⚠️ Erreur lors du spawn (on continue quand même) : {e}")
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Erreur critique boucle timer : {e}")
            await asyncio.sleep(60)

async def spawn_pokeweed(bot: discord.Client):
    channel = bot.get_channel(config.CHANNEL_POKEWEED_ID)
    if not channel:
        logger.warning("❗ Channel Pokéweed introuvable.")
        return

    async with database.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, name, hp, capture_points, power, rarity FROM pokeweeds ORDER BY RAND() LIMIT 1;")
            pokeweed = await cur.fetchone()

    if not pokeweed:
        logger.warning("❗ Aucun Pokéweed trouvé en base.")
        return

    pid, name, hp, cap_pts, power, rarity = pokeweed

    # --- CORRECTION DE LA GESTION DES DOSSIERS ---
    # On enlève les accents aussi sur le dossier pour éviter de chercher 'légendaire'
    # .replace("é", "e") transforme 'Légendaire' en 'legendaire'
    rarity_folder = rarity.lower().replace(" ", "").replace("é", "e").replace("è", "e") 
    
    filename = name.lower().replace(" ", "").replace("é", "e").replace("è", "e") + ".png"
    
    # Chemin final
    image_path = f"./assets/pokeweed/saison-1/{rarity_folder}/{filename}"

    try:
        file = discord.File(image_path, filename=filename)
    except FileNotFoundError:
        # Si l'image n'est pas trouvée, on log l'erreur mais on ne crash pas le bot
        logger.error(f"❌ IMAGE MANQUANTE : {image_path} (Le spawn est annulé pour ce tour)")
        return

    embed = discord.Embed(
        title="👀 Un Pokéweed sauvage est apparu !",
        description=(
            f"🌿 **{name}**\n"
            f"💥 Attaque : {power} | ❤️ Vie : {hp} | ✨ Rareté : {rarity}\n\n"
            f"⚡ Tape **/capture** pour tenter ta chance !"
        ),
        color=0x88CC88
    )
    embed.set_image(url=f"attachment://{filename}")

    await channel.send(file=file, embed=embed)

    state.current_spawn = pokeweed
    state.capture_winner = None


