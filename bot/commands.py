import logging
from datetime import datetime, timezone, date
import random
import os
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands

from . import config, database, helpers

logger = logging.getLogger(__name__)


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

