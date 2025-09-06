import logging
import random
import os
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands
import asyncio
import unicodedata
import re

from . import config, database, helpers, state
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger(__name__)

# -----------------------
# Utils / format helpers
# -----------------------
def format_pokeweed_display(name, power, hp, rarity, owned=0):
    stars = {
        "Commun": "🌿",
        "Peu Commun": "🌱🌿",
        "Rare": "🌟",
        "Très Rare": "💎",
        "Légendaire": "🌈👑",
    }
    flair = {
        "Commun": "",
        "Peu Commun": "*",
        "Rare": "**",
        "Très Rare": "***",
        "Légendaire": "__**"
    }
    flair_end = {
        "Commun": "",
        "Peu Commun": "*",
        "Rare": "**",
        "Très Rare": "***",
        "Légendaire": "**__"
    }

    status = "🆕 Nouvelle carte !" if owned == 0 else f"x{owned + 1}"
    return f"{stars.get(rarity, '🌿')} {flair[rarity]}{name}{flair_end[rarity]} — 💥 {power} | ❤️ {hp} | ✨ {rarity} ({status})"

def _now_utc():
    return datetime.now(timezone.utc)

def _format_remaining(td: timedelta) -> str:
    # retourne "Xh Ymin"
    total_sec = int(td.total_seconds())
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    return f"{hours}h {minutes}min"

# Anti double-clic /booster
_inflight_boosters: set[int] = set()


def setup(bot: commands.Bot):
    # ---------------------------------------
    # /hey
    # ---------------------------------------
    @bot.tree.command(name="hey", description="Parle avec Kanaé, l'IA officielle du serveur !")
    @app_commands.describe(message="Ton message à envoyer")
    async def hey(interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {config.MISTRAL_API_KEY}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "agent_id": config.AGENT_ID_MISTRAL,
                    "messages": [{"role": "user", "content": message}],
                }
                async with session.post(
                    "https://api.mistral.ai/v1/agents/completions",
                    headers=headers,
                    json=payload,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response_text = data['choices'][0]['message']['content']
                    else:
                        response_text = f"Yo, Mistral a répondu {resp.status}. J'sais pas ce qu'il veut là frérot."
        except Exception as e:
            logger.error("Mistral API error: %s", e)
            response_text = "Yo, j'crois que Mistral est en PLS là, réessaye plus tard."
        await interaction.followup.send(response_text, ephemeral=True)

    # ---------------------------------------
    # /score
    # ---------------------------------------
    @bot.tree.command(name="score", description="Affiche ton score ou celui d’un autre membre")
    @app_commands.describe(membre="Le membre dont tu veux voir le score")
    async def score_cmd(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre if membre else interaction.user
        user_id = str(target.id)

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT user_id, points FROM scores ORDER BY points DESC;")
                sorted_rows = await cur.fetchall()

        filtered = []
        for uid, pts in sorted_rows:
            member = interaction.guild.get_member(int(uid))
            if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
                continue
            filtered.append((uid, pts))

        position = None
        user_score = 0
        for i, (uid, pts) in enumerate(filtered, 1):
            if str(uid) == user_id:
                position = i
                user_score = pts
                break

        if position:
            await interaction.response.send_message(
                f"📊 **{target.display_name}** → {user_score} pts (Rang #{position})",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"📊 **{target.display_name}** n’a pas encore de points (ou son rôle est exclu).",
                ephemeral=True
            )

    # ---------------------------------------
    # /top-5
    # ---------------------------------------
    @bot.tree.command(name="top-5", description="Affiche le top 5 des meilleurs fumeurs")
    async def top_5(interaction: discord.Interaction):
        message = await helpers.build_top5_message(
            bot,
            interaction.guild,
            mention_users=False,
            header="🌿 Top 5 Fumeurs Kanaé 🌿",
        )
        if not message:
            await interaction.response.send_message(
                "📊 Pas encore de points enregistrés (ou tous les membres sont exclus).",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(message, ephemeral=True)

    # ---------------------------------------
    # /set (admin)
    # ---------------------------------------
    @bot.tree.command(name="set", description="Définit manuellement le total de points d'un utilisateur")
    @app_commands.describe(user_id="ID Discord de l'utilisateur", nouveau_total="Nombre de points à définir")
    async def set_points(interaction: discord.Interaction, user_id: str, nouveau_total: int):
        try:
            guild = interaction.guild
            member = guild.get_member(int(user_id))
            if not member:
                await interaction.response.send_message("❌ Utilisateur introuvable dans cette guild.", ephemeral=True)
                return
            await database.set_user_points(database.db_pool, user_id, nouveau_total)
            await interaction.response.send_message(f"✅ Le score de {member.display_name} a été mis à **{nouveau_total} points**.", ephemeral=True)
        except Exception as e:
            logger.error("/set failed: %s", e)
            await interaction.response.send_message("❌ Une erreur est survenue en définissant le score.", ephemeral=True)

    # ---------------------------------------
    # /booster (SAFE)
    # ---------------------------------------
    # ✅ VERSION SÛRE ET ILLUSTRÉE DU /booster — commands.py
    _inflight_boosters: set[int] = set()

    def sanitize_filename(name: str) -> str:
        name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
        name = re.sub(r'[^a-zA-Z0-9]', '', name)
        return name.lower()

    @bot.tree.command(name="booster", description="Ouvre un booster de 4 Pokéweeds aléatoires !")
    async def booster(interaction: discord.Interaction):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        # Anti spam/double clic
        if user_id in _inflight_boosters:
            await interaction.response.send_message("⏳ Attends un peu frérot, booster déjà en cours...", ephemeral=True)
            return

        _inflight_boosters.add(user_id)
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)

            # Cooldown check
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT last_opened FROM booster_cooldowns WHERE user_id=%s;", (user_id,))
                    row = await cur.fetchone()
                    if row and row[0]:
                        last_time = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                        if (now - last_time) < timedelta(hours=12):
                            remaining = timedelta(hours=12) - (now - last_time)
                            h, m = remaining.seconds // 3600, (remaining.seconds % 3600) // 60
                            await interaction.edit_original_response(content=f"🕒 Attends encore **{h}h {m}min** pour un nouveau booster.")
                            return

            # Tirage
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT * FROM pokeweeds ORDER BY RAND() LIMIT 4;")
                    rewards = await cur.fetchall()

            points_by_rarity = {"Commun": 2, "Peu Commun": 4, "Rare": 8, "Très Rare": 12, "Légendaire": 15}
            bonus_new = 5
            embeds = []
            files = []
            total_points = 0
            inserts = []

            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for pokeweed in rewards:
                        pid, name, hp, cap_pts, power, rarity = pokeweed[:6]
                        await cur.execute("SELECT COUNT(*) FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s;", (user_id, pid))
                        owned = (await cur.fetchone())[0]

                        # Points
                        pts = points_by_rarity.get(rarity, 0)
                        if owned == 0:
                            pts += bonus_new
                        total_points += pts

                        # Image
                        rarity_folder = rarity.lower().replace(" ", "")
                        filename = sanitize_filename(name) + ".png"
                        image_path = f"./assets/pokeweed/saison-1/{rarity_folder}/{filename}"
                        embed = discord.Embed(
                            title=f"{name} 🌿",
                            description=f"💥 Attaque : {power}\n❤️ Vie : {hp}\n✨ Rareté : {rarity}\n📦 {'🆕 Nouvelle carte !' if owned == 0 else f'x{owned + 1}'}",
                            color=discord.Color.green()
                        )

                        try:
                            file = discord.File(image_path, filename=filename)
                            embed.set_image(url=f"attachment://{filename}")
                            files.append(file)
                        except Exception:
                            embed.description += "\n⚠️ Image non trouvée."

                        embeds.append(embed)
                        inserts.append((user_id, pid))

            # Affichage user
            await interaction.edit_original_response(content=f"🃏 Booster ouvert ! 🎉 Tu gagnes **{total_points} points** dans le concours Kanaé !")
            for embed, file in zip(embeds, files):
                await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                await asyncio.sleep(0.3)

            # MAJ DB finale seulement si tout s'est bien passé
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    for uid, pid in inserts:
                        await cur.execute("INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());", (uid, pid))
                    await database.add_points(database.db_pool, user_id, total_points)
                    await cur.execute("INSERT INTO booster_cooldowns (user_id, last_opened) VALUES (%s, %s) ON DUPLICATE KEY UPDATE last_opened = %s;", (user_id, now, now))

        except Exception as e:
            logger.exception(f"Erreur dans /booster pour {user_id} : {e}")
            await interaction.followup.send("❌ Une erreur est survenue. Réessaie un peu plus tard, rien n'a été consommé.", ephemeral=True)
        finally:
            _inflight_boosters.discard(user_id)


    # ---------------------------------------
    # /capture
    # ---------------------------------------
    @bot.tree.command(name="capture", description="Tente de capturer le Pokéweed sauvage")
    async def capture(interaction: discord.Interaction):
        if not state.current_spawn:
            await interaction.response.send_message("Aucun Pokéweed à capturer maintenant...", ephemeral=True)
            return

        winner_id = getattr(state, "capture_winner", None)
        if winner_id:
            await interaction.response.send_message("Trop tard, il a déjà été capturé !", ephemeral=True)
            return

        pokeweed = state.current_spawn
        user_id = interaction.user.id

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());",
                    (user_id, pokeweed[0])
                )
                points = pokeweed[3]
                await database.add_points(database.db_pool, user_id, points)

        state.capture_winner = user_id
        channel = interaction.channel
        await channel.send(f"🎉 Bravo {interaction.user.mention} pour avoir capturé **{pokeweed[1]}** ! +{pokeweed[3]} points 🌿")
        await interaction.response.send_message("Tu l’as capturé !", ephemeral=True)

    # ---------------------------------------
    # /pokedex
    # ---------------------------------------
    # ✅ VERSION ILLUSTRÉE DU /pokedex
    # À intégrer dans commands.py — affiche chaque Pokéweed possédé avec image (embed par carte)

    def sanitize_filename(name: str) -> str:
        name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
        name = re.sub(r'[^a-zA-Z0-9]', '', name)
        return name.lower()

    @bot.tree.command(name="pokedex", description="Affiche ton Pokédex personnel ou celui d’un autre")
    @app_commands.describe(membre="Le membre dont tu veux voir le Pokédex")
    async def pokedex(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre if membre else interaction.user

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT p.name, p.hp, p.capture_points, p.power, p.rarity,
                        COUNT(*) as total, MAX(up.capture_date) as last_capture
                    FROM user_pokeweeds up
                    JOIN pokeweeds p ON up.pokeweed_id = p.id
                    WHERE up.user_id=%s
                    GROUP BY p.id;
                """, (target.id,))
                rows = await cur.fetchall()

                await cur.execute("SELECT COUNT(*) FROM pokeweeds;")
                total_available = (await cur.fetchone())[0]

        if not rows:
            await interaction.response.send_message(f"📘 {target.display_name} n’a capturé aucun Pokéweed...", ephemeral=True)
            return

        # ENVOI AVEC IMAGES
        await interaction.response.defer(ephemeral=True)
        embeds = []
        files = []

        for name, hp, cap_pts, power, rarity, total, last_date in rows:
            date_str = last_date.strftime("%d %b %Y") if last_date else "?"
            rarity_folder = rarity.lower().replace(" ", "")
            filename = sanitize_filename(name) + ".png"
            path = f"./assets/pokeweed/saison-1/{rarity_folder}/{filename}"

            embed = discord.Embed(
                title=f"{name} 🌿",
                description=f"💥 Attaque : {power}\n❤️ Vie : {hp}\n✨ Points de capture : +{cap_pts}\n📦 Possédé : x{total}\n📅 Dernière capture : {date_str}\n⭐ Rareté : {rarity}",
                color=discord.Color.green()
            )

            if os.path.exists(path):
                file = discord.File(path, filename=filename)
                embed.set_image(url=f"attachment://{filename}")
                files.append(file)
            else:
                embed.description += "\n⚠️ Image non trouvée."

            embeds.append(embed)

        # Envoi par lots de 10 maximum (limite Discord)
        for i in range(len(embeds)):
            try:
                await interaction.followup.send(embed=embeds[i], file=files[i], ephemeral=True)
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.warning(f"❌ Failed to send embed {i}: {e}")

        # Statistiques globales
        unique_count = len(rows)
        total_count = sum([r[5] for r in rows])
        missing = total_available - unique_count

        await interaction.followup.send(
            f"📊 **Stats de collection de {target.display_name}**\n✅ Cartes uniques : {unique_count}/{total_available}\n📦 Total : {total_count} cartes\n❗ Il manque encore **{missing}** Pokéweeds.",
            ephemeral=True
        )

    # ---------------------------------------
    # /init-pokeweeds (admin)
    # ---------------------------------------
    @bot.tree.command(name="init-pokeweeds", description="Insère les 31 Pokéweed de base")
    async def init_pokeweeds(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        strains = [
            ("Gelachu", 100, 10, 40, "Rare", 0.05),
            ("Bulba Kush", 90, 7, 30, "Commun", 0.20),
            ("Sourmander", 110, 9, 35, "Peu Commun", 0.15),
            ("Gluezor", 120, 12, 45, "Rare", 0.05),
            ("OGtortank", 105, 8, 32, "Peu Commun", 0.10),
            ("Widowlee", 95, 6, 28, "Commun", 0.25),
            ("Purplax", 80, 5, 22, "Commun", 0.30),
            ("Skyweedon", 115, 10, 38, "Rare", 0.07),
            ("Pineachu", 85, 7, 25, "Peu Commun", 0.12),
            ("AK-Dracau", 100, 8, 33, "Peu Commun", 0.12),
            ("Zkittlechu", 90, 6, 27, "Commun", 0.20),
            ("Jackasaur", 100, 8, 30, "Peu Commun", 0.10),
            ("Durbanape", 110, 9, 36, "Rare", 0.07),
            ("Lemonix", 95, 6, 26, "Commun", 0.22),
            ("Amnesir", 105, 9, 31, "Peu Commun", 0.10),
            ("Noctulight", 100, 7, 29, "Commun", 0.20),
            ("Weddinja", 110, 11, 37, "Rare", 0.05),
            ("Trainquaza", 100, 9, 34, "Peu Commun", 0.08),
            ("Piekachu", 90, 7, 28, "Commun", 0.22),
            ("Critidos", 105, 8, 32, "Peu Commun", 0.09),
            ("Crackchomp", 95, 6, 27, "Commun", 0.25),
            ("Dosidoof", 100, 8, 31, "Peu Commun", 0.10),
            ("Mimosaur", 90, 6, 26, "Commun", 0.22),
            ("Tangrowth OG", 85, 5, 24, "Commun", 0.30),
            ("Forbiddenite", 115, 12, 40, "Rare", 0.04),
            ("Slurrizard", 100, 8, 33, "Peu Commun", 0.12),
            ("Runflare", 110, 10, 36, "Rare", 0.06),
            ("Gmokémon", 120, 13, 42, "Très Rare", 0.03),
            ("Maclax", 110, 9, 35, "Rare", 0.05),
            ("Sherbizard", 95, 7, 29, "Commun", 0.22),
            ("Kanéclor", 150, 20, 60, "Légendaire", 0.01)
        ]

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for s in strains:
                    await cur.execute("INSERT INTO pokeweeds (name, hp, capture_points, power, rarity, drop_rate) VALUES (%s,%s,%s,%s,%s,%s);", s)

        await interaction.response.send_message("🌿 31 Pokéweed insérés !", ephemeral=True)

    # ---------------------------------------
    # /reset-scores (admin)
    # ---------------------------------------
    @bot.tree.command(name="reset-scores", description="Réinitialise tous les scores du concours à 0 (ADMIN uniquement)")
    async def reset_scores(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Tu dois être administrateur pour faire ça frérot.", ephemeral=True)
            return

        try:
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("UPDATE scores SET points = 0;")
            await interaction.response.send_message("✅ Tous les scores ont été réinitialisés à **0** pour le concours.", ephemeral=False)
            logger.info("Tous les scores du concours ont été remis à zéro.")
        except Exception as e:
            logger.error("/reset-scores failed: %s", e)
            await interaction.response.send_message("❌ Erreur lors de la remise à zéro des scores.", ephemeral=True)
