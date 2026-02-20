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
                    pokeweed_channel = interaction.client.get_channel(config.CHANNEL_POKEWEED_ID)

                    stars = {
                        "Commun": "🌿",
                        "Peu Commun": "🌱🌿",
                        "Rare": "🌟",
                        "Très Rare": "💎",
                        "Légendaire": "🌈👑",
                    }

                    resume_lines = [
                        "🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀",
                        "",
                        f"🎉 {interaction.user.mention} a ouvert un **booster** et a obtenu :",
                        ""
                    ]

                    for pokeweed in rewards:
                        pid, name, hp, cap_pts, power, rarity = pokeweed[:6]
                        resume_lines.append(f"{stars.get(rarity, '🌿')} {name} — 💥 {power} | ❤️ {hp} | ✨ {rarity}")

                    resume_lines.append("")
                    resume_lines.append("🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀🌀")  # ✅ ligne d'emojis en bas

                    resume_message = "\n".join(resume_lines)

                    if pokeweed_channel:
                        await pokeweed_channel.send(resume_message)

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
                        rarity_folder = rarity.lower().replace(" ", "").replace("é", "e")
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

    # ✅ VERSION INTERACTIVE DU /pokedex AVEC BOUTONS PAR RARETÉ

    RARITY_ORDER = [
        ("Commun", "🌿"),
        ("Peu Commun", "🌱🌿"),
        ("Rare", "🌟"),
        ("Très Rare", "💎"),
        ("Légendaire", "🌈👑")
    ]

    def sanitize_filename(name: str) -> str:
        import unicodedata, re
        name = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode('utf-8')
        return re.sub(r'[^a-zA-Z0-9]', '', name).lower()

    class RarityButton(discord.ui.Button):
        def __init__(self, rarity, emoji, user, pokes):
            label = f"{emoji} {rarity}"
            super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=rarity)
            self.rarity = rarity
            self.user = user
            self.pokes = pokes

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)

            if interaction.user.id != self.user.id:
                await interaction.followup.send("❌ Ce Pokédex n’est pas le tien.", ephemeral=True)
                return

            if not self.pokes:
                await interaction.followup.send(f"📭 Tu n’as aucun Pokéweed de rareté **{self.rarity}**.", ephemeral=True)
                return

            for name, hp, cap_pts, power, rarity_val, total, last_date in self.pokes:
                filename = sanitize_filename(name) + ".png"
                path = f"./assets/pokeweed/saison-1/{rarity_val.lower().replace(' ', '').replace('é', 'e')}/{filename}"
                date_str = last_date.strftime("%d %b %Y") if last_date else "?"

                embed = discord.Embed(
                    title=f"{name} 🌿",
                    description=f"💥 Attaque : {power}\n❤️ Vie : {hp}\n✨ Capture : +{cap_pts}\n📦 Possédé : x{total}\n📅 Dernière capture : {date_str}\n⭐ Rareté : {rarity_val}",
                    color=discord.Color.green()
                )

                if os.path.exists(path):
                    file = discord.File(path, filename=filename)
                    embed.set_image(url=f"attachment://{filename}")
                    await interaction.followup.send(embed=embed, file=file, ephemeral=True)
                else:
                    embed.description += "\n⚠️ Image non trouvée."
                    await interaction.followup.send(embed=embed, ephemeral=True)

                await asyncio.sleep(0.2)

            # ✅ Une fois TOUS les pokéweeds envoyés, on redonne les boutons
            await interaction.followup.send(
                content="👀 Tu veux regarder une autre rareté ? Clique sur un autre bouton ci-dessous.",
                view=RarityView(self.view.pokemons_by_rarity, self.view.user),
                ephemeral=True
            )

    class RarityView(discord.ui.View):
        def __init__(self, pokemons_by_rarity: dict, user: discord.User):
            super().__init__(timeout=300)
            self.pokemons_by_rarity = pokemons_by_rarity  # ✅ Ajouté
            self.user = user  # ✅ Ajouté

            for rarity, emoji in RARITY_ORDER:
                pokes = pokemons_by_rarity.get(rarity, [])
                self.add_item(RarityButton(rarity, emoji, user, pokes))

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

        pokemons_by_rarity = {}
        for row in rows:
            pokemons_by_rarity.setdefault(row[4], []).append(row)

        unique_count = len(rows)
        total_count = sum(r[5] for r in rows)
        missing = total_available - unique_count

        summary = (
            f"📘 **Pokédex de {target.display_name}**\n\n"
            f"✅ Cartes uniques : {unique_count}/{total_available}\n"
            f"📦 Total : {total_count} cartes\n"
            f"❗ Il manque encore **{missing}** Pokéweeds pour compléter le Pokédex !\n\n"
            "👆 Clique sur les boutons ci-dessous pour afficher les Pokéweeds par rareté.\n\n"
            "\u200b"
        )

        await interaction.response.send_message(
            summary,
            view=RarityView(pokemons_by_rarity, target),
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

    # ---------------------------------------
    # /link-twitch
    # ---------------------------------------
    @bot.tree.command(name="link-twitch", description="Lie ton compte Twitch pour gagner des points sur le live !")
    @app_commands.describe(pseudo_twitch="Ton pseudo exact sur Twitch (sans le @)")
    async def link_twitch(interaction: discord.Interaction, pseudo_twitch: str):
        user_id = interaction.user.id
        platform = "twitch"
        
        # 1. On vérifie s'il est déjà lié
        existing_twitch = await database.get_social_by_discord(database.db_pool, user_id, platform)
        if existing_twitch:
            await interaction.response.send_message(
                f"⚠️ Frérot, ton compte Discord est déjà lié au pseudo Twitch **{existing_twitch}** !\n"
                f"Si tu veux changer, utilise d'abord la commande `/unlink-twitch`.", 
                ephemeral=True
            )
            return

        clean_pseudo = pseudo_twitch.strip().lower()
        await interaction.response.defer(ephemeral=True) # On defer car l'API peut prendre 1 ou 2 secondes
        
        try:
            success = await database.link_social_account(database.db_pool, user_id, platform, clean_pseudo)
            
            if success:
                msg = f"✅ Frérot, ton compte Discord est maintenant lié au pseudo Twitch **{clean_pseudo}** !\n"
                
                # 🌿 On vérifie s'il FOLLOW la chaîne avec DecAPI
                async with aiohttp.ClientSession() as session:
                    url = f"https://decapi.me/twitch/followage/{config.TWITCH_CHANNEL}/{clean_pseudo}"
                    async with session.get(url) as resp:
                        follow_text = await resp.text()
                
                # Si le texte contient ces mots, c'est qu'il ne follow pas ou que le pseudo n'existe pas
                is_following = "does not follow" not in follow_text.lower() and "error" not in follow_text.lower() and "not found" not in follow_text.lower()
                
                if is_following:
                    # ON DONNE LA RÉCOMPENSE (Seulement la première fois)
                    can_reward = await database.check_and_reward_social_link(database.db_pool, user_id, platform)
                    if can_reward:
                        await database.add_points(database.db_pool, user_id, 200)
                        msg += f"\n🎁 **BOOM !** On a vu que tu follow déjà la chaîne ! Tu gagnes **+200 points** direct ! 🌿"
                    else:
                        msg += "\nPrépare-toi à amasser les points pour le Kanaé d'Or quand le live sera ON 📺🌿"
                else:
                    msg += f"\n⚠️ **Attention :** Tu ne follow pas encore la chaîne **{config.TWITCH_CHANNEL}** !\n👉 Follow le live et tape la commande `/claim-twitch` pour récupérer tes 200 points !"
                
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.followup.send(
                    f"❌ Hop hop hop ! Le pseudo Twitch **{clean_pseudo}** est déjà utilisé par un autre membre du serveur.\n"
                    f"Chacun sa batte, mets ton vrai pseudo !", 
                    ephemeral=True
                )
        except Exception as e:
            logger.error("Erreur link-twitch: %s", e)
            await interaction.followup.send("❌ Une erreur est survenue en base de données. Réessaie plus tard.", ephemeral=True)

    
    # ---------------------------------------
    # /unlink-twitch
    # ---------------------------------------
    @bot.tree.command(name="unlink-twitch", description="Délie ton compte Twitch de ton profil Discord")
    async def unlink_twitch(interaction: discord.Interaction):
        user_id = interaction.user.id
        platform = "twitch"
        
        existing_twitch = await database.get_social_by_discord(database.db_pool, user_id, platform)
        
        if not existing_twitch:
            await interaction.response.send_message(
                "❌ T'as aucun compte Twitch lié pour le moment frérot. Tu peux utiliser `/link-twitch` pour en ajouter un !", 
                ephemeral=True
            )
            return
            
        try:
            await database.unlink_social_account(database.db_pool, user_id, platform)
            await interaction.response.send_message(
                f"🗑️ C'est fait ! Ton ancien pseudo Twitch (**{existing_twitch}**) a été délié de ton compte.\n"
                f"Tu peux maintenant en lier un nouveau si tu veux.", 
                ephemeral=True
            )
        except Exception as e:
            logger.error("Erreur unlink-twitch: %s", e)
            await interaction.response.send_message(
                "❌ Une erreur est survenue lors de la suppression. Réessaie plus tard.", 
                ephemeral=True
            )
    # ---------------------------------------
    # /refresh-points
    # ---------------------------------------
    @bot.tree.command(name="refresh-points", description="Vérifie tous tes réseaux (Follow, Sub Twitch...) pour récupérer tes points !")
    async def refresh_points(interaction: discord.Interaction):
        user_id = interaction.user.id
        await interaction.response.defer(ephemeral=True) # On fait patienter car on check plusieurs trucs sur internet
        
        # 1. On récupère le pseudo Twitch lié
        twitch_user = await database.get_social_by_discord(database.db_pool, user_id, "twitch")
        
        if not twitch_user:
            await interaction.followup.send("❌ Tu n'as lié aucun compte pour le moment ! Commence par faire `/link-twitch`.", ephemeral=True)
            return
            
        report = ["🔄 **COMPTE RENDU DE TES RÉSEAUX** 🔄", ""]
        total_gained = 0
        
        async with aiohttp.ClientSession() as session:
            # --- VERIFICATION 1 : TWITCH FOLLOW (200 pts) ---
            follow_url = f"https://decapi.me/twitch/followage/{config.TWITCH_CHANNEL}/{twitch_user}"
            async with session.get(follow_url) as resp:
                follow_text = await resp.text()
            
            is_following = "does not follow" not in follow_text.lower() and "error" not in follow_text.lower() and "not found" not in follow_text.lower()
            
            if is_following:
                # On check s'il a déjà eu la récompense
                can_reward_follow = await database.check_and_reward_social_link(database.db_pool, user_id, "twitch")
                if can_reward_follow:
                    total_gained += 200
                    report.append(f"✅ **Twitch Follow :** 🎁 +200 points ! Merci pour le soutien frérot !")
                else:
                    report.append(f"✅ **Twitch Follow :** Déjà récupéré ! 🌿")
            else:
                report.append(f"❌ **Twitch Follow :** Tu ne follow pas encore la chaîne.")

            # --- VERIFICATION 2 : TWITCH SUB (1000 pts / MOIS) ---
            sub_url = f"https://decapi.me/twitch/subage/{config.TWITCH_CHANNEL}/{twitch_user}"
            async with session.get(sub_url) as resp:
                sub_text = await resp.text()
            
            is_subbed = "not subscribed" not in sub_text.lower() and "does not subscribe" not in sub_text.lower() and "error" not in sub_text.lower() and "not found" not in sub_text.lower()
            
            if is_subbed:
                # 🌿 On utilise le nouveau système de Cooldown Mensuel !
                can_reward_sub = await database.claim_twitch_sub_reward(database.db_pool, user_id)
                if can_reward_sub:
                    total_gained += 1000
                    report.append(f"💎 **Twitch Sub :** 🎁 +1000 points ! Masterclass le sub, t'es un roi ! 👑")
                else:
                    report.append(f"💎 **Twitch Sub :** Toujours abonné, mais tu as déjà récupéré tes points ce mois-ci ! Reviens le mois prochain. 🔥")
            else:
                report.append(f"❌ **Twitch Sub :** Tu n'es pas abonné (Sub) à la chaîne.")

        # --- BILAN DES POINTS ---
        if total_gained > 0:
            await database.add_points(database.db_pool, user_id, total_gained)
            report.append("")
            report.append(f"🎉 **TOTAL GAGNÉ À L'INSTANT : +{total_gained} points !**")
        else:
            report.append("")
            report.append("🤷‍♂️ Aucun nouveau point à récupérer pour le moment.")
            
        await interaction.followup.send("\n".join(report), ephemeral=True)

    # ---------------------------------------
    # /spawn (admin)
    # ---------------------------------------
    @bot.tree.command(name="spawn", description="Force le spawn immédiat d’un Pokéweed (admin only)")
    async def spawn_cmd(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        from . import tasks  # importe tes tâches (dont spawn_pokeweed)

        await interaction.response.defer(ephemeral=True)
        try:
            await tasks.spawn_pokeweed(bot)
            await interaction.followup.send("✅ Un Pokéweed vient de spawn dans le channel dédié !", ephemeral=True)
        except Exception as e:
            logger.exception("Erreur dans /spawn : %s", e)
            await interaction.followup.send(f"❌ Une erreur est survenue : {e}", ephemeral=True)

    @bot.tree.command(name="vibe-setup", description="(Admin) Publie le message de rôles (weed/shit) et pose les réactions")
    async def vibe_setup(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
            return

        channel = bot.get_channel(config.REACTION_ROLE_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message("❌ Salon introuvable (vérifie REACTION_ROLE_CHANNEL_ID).", ephemeral=True)
            return

        guild = interaction.guild
        weed_role = guild.get_role(config.WEED_ROLE_ID)
        shit_role = guild.get_role(config.SHIT_ROLE_ID)
        if not weed_role or not shit_role:
            await interaction.response.send_message("❌ Rôle(s) introuvable(s) (vérifie WEED_ROLE_ID / SHIT_ROLE_ID).", ephemeral=True)
            return

        # Le message affiché
        lines = [
            "🥦 **Choisis ta vibe !** 🍫",
            "",
            "Impose ton choix, et montre à tout le monde ce que tu préfères 🧑‍🚀",
            "",
            f"{config.EMOJI_WEED} Team WEED → {weed_role.mention}",
            f"{config.EMOJI_SHIT} Team SHIT → {shit_role.mention}",
            "",
            "_Ajoute la réaction que tu souhaites pour **prendre** le rôle, retire-la pour **l’enlever** ✅ ._",
        ]
        await interaction.response.defer(ephemeral=True)
        message = await channel.send("\n".join(lines))

        # Ajoute les réactions
        for emoji in (config.EMOJI_WEED, config.EMOJI_SHIT):
            try:
                await message.add_reaction(emoji)
            except Exception:
                pass

        # Sauvegarde runtime + feedback
        from . import state
        state.weed_shit_message_id = message.id
        await interaction.followup.send(
            f"✅ Reaction roles prêts dans {channel.mention}.\nMessage ID: `{message.id}`",
            ephemeral=True
        )
