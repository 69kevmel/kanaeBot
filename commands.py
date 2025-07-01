import logging
import random
import os
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands

from . import config, database, helpers, state
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger(__name__)

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


def setup(bot: commands.Bot):
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

    @bot.tree.command(name="score", description="Affiche ta place et ton score dans le classement actuel")
    async def score_cmd(interaction: discord.Interaction):
        user_id = str(interaction.user.id)
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
                f"📊 **{interaction.user.display_name}** ({user_score} pts) → Rang #{position}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"📊 **{interaction.user.display_name}**, tu n'as pas encore de points (ou ton rôle est exclu).",
                ephemeral=True,
            )

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

    @bot.tree.command(name="send-first-top-5", description="Envoie immédiatement le Top 5 et démarre le cycle")
    async def send_first_top_5(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Permission refusée.", ephemeral=True)
            return
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
            return
        msg = await helpers.build_top5_message(
            bot,
            interaction.guild,
            mention_users=True,
            header="🌟 Hall of Flamme — TOP 5 Kanaé 🌟",
        )
        if not msg:
            await interaction.response.send_message("📊 Pas encore de points enregistrés.", ephemeral=True)
            return
        msg += (
            "\n\nRespect à vous les frérots, vous envoyez du très lourd ! Continuez comme ça, le trône du **Kanaé d’Or ** vous attend ! 🛋️🌈"
            "\n\n🌿 Restez chill, partagez la vibe. Kanaé représente ! 🌿"
        )
        await channel.send(msg)
        await database.mark_recap_sent(database.db_pool, date.today())
        await interaction.response.send_message("✅ Top 5 envoyé et cycle lancé !", ephemeral=True)

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

    @bot.tree.command(name="launch-concours", description="Lance officiellement un concours")
    async def launch_concours(interaction: discord.Interaction):
        channel_to_post = bot.get_channel(config.BLABLA_CHANNEL_ID)
        if not channel_to_post:
            await interaction.response.send_message("❗ Le channel ‘blabla’ est introuvable.", ephemeral=True)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="🏟️ Aller au Hall of Flamme",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{interaction.guild.id}/{config.HALL_OF_FLAMME_CHANNEL_ID}"
        ))
        content = (
            "🔥 **Le concours Kanaé est officiellement lancé !** 🔥\n\n"
            "📸 **Postez vos photos ou vidéos dans les salons « montre ton ».**\n"
            "   • 15 points par média (1 fois par jour par salon) 🌿📷\n\n"
            "🎙️ **Restez en vocal pour gagner des points !**\n"
            "   • 1 point toutes les 30 minutes passées en salon vocal 🎧⏳\n\n"
            "✨ **Faites-vous liker !**\n"
            "   • 2 points par émoji laissé par un autre membre sur votre message ✨👍\n"
            "     (1 émoji max par membre et par message) 👀\n\n"
            "🔗 **Parrainez vos potes !**\n"
            "   • 100 points si le nouveau membre reste **au moins 2 heures** sur le serveur 🔗🚀\n\n"
            "🏆 **Chaque semaine, on fera un Top 3 !**\n"
            "   • Classement hebdo 📈\n\n"
            "💰 **Ce mois-ci, le grand gagnant recevra chez lui 25 € de matos (feuilles, briquet, grinder, etc.) !** 🎉💵\n\n"
            "🌟 **Restez branchés, et surtout, kiffez !** 🌺🌀\n"
            "@everyone, c’est parti pour le concours Kanaé !\n\n"
            "👉 Clique sur **« Aller au Hall of Flamme »** ci-dessous pour suivre le classement en temps réel ! 🔥"
        )
        await channel_to_post.send(content, view=view)
        await interaction.response.send_message("✅ Concours lancé dans #blabla !", ephemeral=True)

    @bot.tree.command(name="présentation-concours", description="Présente les règles du concours")
    async def presentation_concours(interaction: discord.Interaction):
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
            return
        content = (
            "📜 **Présentation du Concours Kanaé :**\n\n"
            "Bienvenue à tous ! Voici les règles du jeu :\n"
            "1. **Postez une photo ou vidéo** dans l'un des salons « montre ton ».\n"
            "   • **15 points par jour** et par salon. (max. 1 média/jour/salon) 📸🌿\n\n"
            "2. **Restez en vocal** pour gagner des points : **1 point toutes les 30 minutes**. 🎙️⏳\n\n"
            "3. **Réactions** : chaque émoji laissé par un autre membre sur votre message = **2 points** ✨👍\n"
            "   (1 émoji max par membre et par message) 👀\n\n"
            "4. **Parrainage** : **+100 points** si le nouveau membre reste **au moins 2 heures** sur le serveur 🔗🚀\n\n"
            "🏆 **Les gains ?** Suffit d’être premier et ce mois-ci tu gagneras **25 € de matos de fume** (feuille, grinder, etc.) ! 💰🎉\n"
            "🥇 **C’est tout ?** Ah et bien sûr vous aurez le rôle le plus convoité du serveur : **Kanaé d’or** ! 🌟🏅\n"
            "📆 **Récap chaque lundi à 15 h du Top 3 dans ce channel**. 📊🗓️\n"
            "📢 **Fin du concours** le 1er juillet 2025. ⏰🚩\n\n"
            "Bonne chance à tous, restez chill, et amusez-vous ! 🌿😎\n\n"
            "🔧 **Commandes utiles à connaître :**\n"
            "   • `/score` : Affiche TON score et ton rang actuel. 📈🔒\n"
            "   • `/top-5` : Affiche le Top 5 des meilleurs fumeurs du concours. 🏆✉️\n"
            "@everyone, c’est parti !"
        )
        await channel.send(content)
        await interaction.response.send_message("✅ Présentation du concours postée !", ephemeral=True)

    @bot.tree.command(name="pre-end", description="Envoie un message de boost avant la fin du concours")
    async def pre_end(interaction: discord.Interaction):
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
            return
        content = (
            "⚡ **Attention, il ne reste que quelques heures avant la fin du concours !** ⚡\n"
            "Donnez tout ce qui vous reste, postez vos meilleures photos/vidéos, et préparez-vous pour le décompte final ! 🌿🔥\n"
            "@everyone, c’est le moment de briller !\n\n"
        )
        await channel.send(content)
        await interaction.response.send_message("✅ Message de pré-fin envoyé !", ephemeral=True)

    @bot.tree.command(name="end-concours", description="Annonce la fin du concours")
    async def end_concours(interaction: discord.Interaction):
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
            return
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT user_id, points FROM scores ORDER BY points DESC;")
                all_rows = await cur.fetchall()
        guild = channel.guild
        podium = []
        for uid, pts in all_rows:
            member = guild.get_member(int(uid))
            if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
                continue
            podium.append((uid, pts))
            if len(podium) >= 3:
                break
        content = "🏁 **Le concours est maintenant terminé !** 🏁\n\n**Résultats :**\n"
        for i, (user_id, points) in enumerate(podium, 1):
            user = await bot.fetch_user(int(user_id))
            content += f"{i}. {user.display_name} ({points} pts)\n"
        content += "\nFélicitations aux gagnants et merci à tous d'avoir participé ! 🎉\n@everyone"
        await channel.send(content)
        await interaction.response.send_message("✅ Concours terminé et résultats postés !", ephemeral=True)

    @bot.tree.command(name="booster", description="Ouvre un booster de 4 Pokéweeds aléatoires !")
    async def booster(interaction: discord.Interaction):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Vérifie le cooldown 12h
                await cur.execute("SELECT last_opened FROM booster_cooldowns WHERE user_id=%s;", (user_id,))
                row = await cur.fetchone()
                if row and row[0]:
                    last_time = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                    if (now - last_time) < timedelta(hours=12):
                        remaining = timedelta(hours=12) - (now - last_time)
                        hours = remaining.seconds // 3600
                        minutes = (remaining.seconds % 3600) // 60
                        await interaction.response.send_message(
                            f"🕒 Tu dois attendre encore **{hours}h {minutes}min** avant d’ouvrir un nouveau booster frérot.",
                            ephemeral=True
                        )
                        return

                # Mets à jour le cooldown
                await cur.execute(
                    "INSERT INTO booster_cooldowns (user_id, last_opened) VALUES (%s, %s) ON DUPLICATE KEY UPDATE last_opened = %s;",
                    (user_id, now, now)
                )

                # Tire 4 Pokéweeds aléatoires
                await cur.execute("SELECT * FROM pokeweeds ORDER BY RAND() LIMIT 4;")
                rewards = await cur.fetchall()

                # Barème points par rareté
                points_by_rarity = {
                    "Commun": 2,
                    "Peu Commun": 4,
                    "Rare": 8,
                    "Très Rare": 12,
                    "Légendaire": 15,
                }
                bonus_new = 5

                total_points = 0
                desc_lines = []

                for r in rewards:
                    pokeweed_id = r[0]
                    name = r[1]
                    hp = r[2]
                    capture_points = r[3]
                    power = r[4]
                    rarity = r[5]

                    # Check si déjà possédé
                    await cur.execute(
                        "SELECT COUNT(*) FROM user_pokeweeds WHERE user_id=%s AND pokeweed_id=%s;",
                        (user_id, pokeweed_id)
                    )
                    count = await cur.fetchone()
                    owned = count[0] if count else 0

                    # Ajoute la carte
                    await cur.execute(
                        "INSERT INTO user_pokeweeds (user_id, pokeweed_id, capture_date) VALUES (%s, %s, NOW());",
                        (user_id, pokeweed_id)
                    )

                    # Calcule les points
                    pts = points_by_rarity.get(rarity, 0)
                    if owned == 0:
                        pts += bonus_new
                    total_points += pts

                    # Format texte
                    status = "🆕 Nouvelle carte !" if owned == 0 else f"x{owned + 1}"
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
                    flair_end = flair  # même fermeture que l'ouverture
                    desc_lines.append(
                        f"{stars.get(rarity, '🌿')} {flair[rarity]}{name}{flair_end[rarity]} "
                        f"— 💥 {power} | ❤️ {hp} | ✨ {rarity} ({status})"
                    )

                # Ajoute les points au score global
                await database.add_points(database.db_pool, user_id, total_points)

        # Messages
        desc = "\n".join(desc_lines)
        border = "🌀" * 12

        # DM privé stylisé
        await interaction.response.send_message(
            f"🃏 Ouverture du booster... ✨\n\n"
            f"{desc}\n\n"
            f"🎖️ Tu gagnes **{total_points} points** dans le concours Kanaé !",
            ephemeral=True
        )

        # Annonce publique
        channel = interaction.guild.get_channel(config.CHANNEL_POKEWEED_ID)
        if channel:
            await channel.send(
                f"{border}\n\n"
                f"📦 **{interaction.user.display_name}** a ouvert un booster :\n\n"
                f"{desc}\n\n"
                f"🎖️ +{total_points} points pour le concours !\n\n"
                f"{border}"
            )


   
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
 
    @bot.tree.command(name="pokedex", description="Affiche ton Pokédex personnel ou celui d’un autre")
    @app_commands.describe(membre="@ Le membre dont tu veux voir le Pokédex")
    async def pokedex(interaction: discord.Interaction, membre: discord.Member = None):
        target = membre if membre else interaction.user

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT 
                        p.name, p.hp, p.capture_points, p.power, p.rarity,
                        COUNT(*) as total,
                        MAX(up.capture_date) as last_capture
                    FROM user_pokeweeds up 
                    JOIN pokeweeds p ON up.pokeweed_id = p.id 
                    WHERE up.user_id=%s 
                    GROUP BY p.id;
                """, (target.id,))
                rows = await cur.fetchall()

                await cur.execute("SELECT COUNT(*) FROM pokeweeds;")
                total_available = (await cur.fetchone())[0]

        if not rows:
            await interaction.response.send_message(
                f"📘 {target.display_name} n’a capturé aucun Pokéweed...", ephemeral=True
            )
            return

        # Format d’affichage stylé
        def format_entry(name, hp, points, power, rarity, total, last_date):
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
            last_seen = last_date.strftime("%d %b %Y") if last_date else "?"
            return (
                f"{stars.get(rarity, '🌿')} {flair[rarity]}{name}{flair_end[rarity]}\n"
                f"• Rareté : {rarity}\n"
                f"• 💥 Attaque : {power}\n"
                f"• ❤️ Vie : {hp}\n"
                f"• ✨ Points de capture : +{points}\n"
                f"• 📦 Possédé : x{total}\n"
                f"• 📅 Dernière capture : {last_seen}\n"
            )

        entries = "\n".join([
            format_entry(name, hp, points, power, rarity, total, last_date)
            for name, hp, points, power, rarity, total, last_date in rows
        ])

        unique_count = len(rows)
        total_count = sum([total for *_, total, _ in rows])
        missing = total_available - unique_count

        summary = (
            f"\n📊 **Statistiques de collection**\n"
            f"✅ Cartes différentes : {unique_count}/{total_available}\n"
            f"📦 Total de cartes collectées : {total_count}\n"
            f"❗ Il te manque encore **{missing}** Pokéweed{'s' if missing > 1 else ''} pour compléter le Pokédex !"
        )

        full_message = f"📘 Pokédex de {target.display_name} :\n\n{entries}{summary}"

        # Discord limite à 2000 caractères
        MAX_LENGTH = 2000

        if len(full_message) <= MAX_LENGTH:
            await interaction.response.send_message(full_message, ephemeral=True)
        else:
            await interaction.response.send_message(
                "⚠️ Ton Pokédex est trop grand pour un seul message ! Je t'envoie en plusieurs parties :", ephemeral=True
            )
            for i in range(0, len(full_message), MAX_LENGTH):
                await interaction.followup.send(full_message[i:i+MAX_LENGTH], ephemeral=True)



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
