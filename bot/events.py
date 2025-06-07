import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from . import config, database, helpers, state, tasks

logger = logging.getLogger(__name__)

class InfosConcoursButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="ℹ️ Infos Concours", custom_id="infos_concours"))

    @discord.ui.button(label="ℹ️ Infos Concours", style=discord.ButtonStyle.primary, custom_id="infos_concours")
    async def concours_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = (
            "🌿 **Le Concours Kanaé :**\n\n"
            "👉 **Gagne des points en postant des photos ou vidéos dans les salons spéciaux :**\n"
            "   • 📸 15 points par média (1 fois par jour par salon)\n\n"
            "👉 **Gagne des points en passant du temps en vocal :**\n"
            "   • 🎙️ 1 point toutes les 30 minutes\n\n"
            "👉 **Gagne des points avec les réactions :**\n"
            "   • ✨ 2 points par émoji reçu sur ton message (1 émoji max par membre et par message)\n\n"
            "👉 **Bonus Parrainage :**\n"
            "   • 🔗 100 points si le nouveau membre reste **au moins 2 heures** sur le serveur\n\n"
            "🎯 **Les paliers à atteindre :**\n"
            "   • 🥉 10 points ➔ Bravo frérot !\n"
            "   • 🥈 50 points ➔ Respect, t'es chaud !\n"
            "   • 🏆 100 points ➔ Légende vivante !\n\n"
            "📊 **Classements chaque lundi à 15h (Top 3) et reset mensuel.**\n\n"
            "🔥 Viens chiller, poster et papoter, et deviens le **Kanaé d'Or** de la commu !"
        )
        await interaction.response.send_message(message, ephemeral=True)


def setup(bot: commands.Bot):
    @bot.event
    async def on_ready():
        logger.info("Bot ready, initializing database")
        try:
            database.db_pool = await database.init_db_pool()
            await database.ensure_tables(database.db_pool)
        except Exception as e:
            logger.error("Failed to init DB: %s", e)
            return

        for guild in bot.guilds:
            try:
                state.invite_cache[guild.id] = await guild.invites()
            except Exception as e:
                logger.warning("Failed to fetch invites for %s: %s", guild.name, e)
        logger.info("KanaéBot prêt en tant que %s", bot.user)
        try:
            synced = await bot.tree.sync()
            logger.info("%d slash commands synced", len(synced))
        except Exception as e:
            logger.error("Slash command sync failed: %s", e)
        tasks.weekly_recap.start(bot)
        tasks.daily_scores_backup.start(bot)
        tasks.update_voice_points.start(bot)
        bot.loop.create_task(tasks.fetch_and_send_news(bot))
    @bot.event
    async def on_member_join(member: discord.Member):
        try:
            guild = member.guild
            invites_before = state.invite_cache.get(guild.id, [])
            invites_after = await guild.invites()
            used_invite = None
            for invite in invites_after:
                for old_invite in invites_before:
                    if invite.code == old_invite.code and invite.uses > old_invite.uses:
                        used_invite = invite
                        break
                if used_invite:
                    break
            state.invite_cache[guild.id] = invites_after
            if used_invite and used_invite.inviter:
                inviter = used_invite.inviter
                inviter_id = str(inviter.id)
                async def award_after_2h():
                    await asyncio.sleep(7200)
                    if member.id in [m.id for m in guild.members]:
                        new_total = await database.add_points(database.db_pool, inviter_id, 100)
                        await helpers.safe_send_dm(inviter,
                            f"🎉 Bravo frérot ! +100 points pour ton parrainage de `{member.name}`, "
                            f"il est resté 2 h sur le serveur ! Total : {new_total} points. Continue comme ça 🚀")
                asyncio.create_task(award_after_2h())
        except Exception as e:
            logger.warning("Parrainage detection failed: %s", e)
        try:
            view = InfosConcoursButton()
            view.add_item(discord.ui.Button(
                label="📜 Règlement", style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{member.guild.id}/{config.CHANNEL_REGLES_ID}"
            ))
            view.add_item(discord.ui.Button(
                label="🙋 Présente-toi", style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{member.guild.id}/{config.CHANNEL_PRESENTE_TOI_ID}"
            ))
            view.add_item(discord.ui.Button(
                label="🌿 Montre ta batte", style=discord.ButtonStyle.link,
                url=f"https://discord.com/channels/{member.guild.id}/{config.CHANNEL_MONTRE_TA_BATTE_ID}"
            ))
            message = (
                f"🌿 Yo {member.name} ! Bienvenue dans le cercle **{member.guild.name}**.\n\n"
                "Ici, ça chill, ça partage, et ça kiffe. **0 pression**. 😎\n"
                "Que tu sois là pour montrer ta dernière **batte** 🌿, ton **matos** 🔥, ou juste pour papoter 💬, **t'es chez toi**.\n\n"
                "Avant de te lancer, check les règles 📜 et **présente-toi** 🙋 (Montre qui t'es, en fait).\n\n"
                "Ensuite, n'hésite pas à découvrir les autres salons et à te balader 🚀.\n\n"
                "**(👻 Discret ? Si tu veux changer ton pseudo, clique droit sur ton profil à droite et choisis 'Changer le pseudo')**\n\n"
                "Quelques commandes utiles :\n"
                "   ➡️ **/play** {nom de la musique} - Pour écouter de la musique dans le channel **KanaéMUSIC** 🎶\n"
                "   ➡️ **/hey** {message} - Pour parler avec l'**IA officielle** de **Kanaé** 🤖\n"
                "   ➡️ **/score** - Pour voir **ta place** dans le concours de **Kanaé** 🎖️\n"
                "   ➡️ **/top-5** - Pour voir les **5 plus gros fumeurs** du concours de **Kanaé** 🏆\n\n"
            )
            await helpers.safe_send_dm(member, message)
            logger.info("Welcome DM sent to %s", member.name)
        except Exception as e:
            logger.warning("Failed to send welcome DM: %s", e)

    @bot.event
    async def on_message(message: discord.Message):
        if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
            user_id = str(message.author.id)
            count = state.user_dm_counts.get(user_id, 0)
            if count == 0:
                response = "Salut frérot, écoute je peux pas te répondre là, viens sur le serveur Kanaé :)"
            elif count == 1:
                response = "Gros t'as pas compris je crois, viens sur le serveur direct !"
            elif count == 2:
                response = "Frr laisse tomber, j'arrête de parler ici, viens sur le serv chui trop démarré là."
            else:
                return
            try:
                await message.channel.send(response)
                state.user_dm_counts[user_id] = count + 1
                logger.info("DM response sent to %s", message.author)
            except Exception as e:
                logger.warning("Failed to reply to DM: %s", e)
        if not message.author.bot and isinstance(message.channel, discord.TextChannel):
            user_id = str(message.author.id)
            channel_id = message.channel.id
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if channel_id in config.SPECIAL_CHANNEL_IDS and message.attachments:
                image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
                video_extensions = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv")
                has_media = any(
                    attachment.filename.lower().endswith(image_extensions + video_extensions)
                    for attachment in message.attachments
                )
                if has_media:
                    if not await database.has_daily_limit(database.db_pool, user_id, channel_id, date_str):
                        await database.set_daily_limit(database.db_pool, user_id, channel_id, date_str)
                        new_total = await database.add_points(database.db_pool, user_id, config.SPECIAL_CHANNEL_IDS[channel_id])
                        if new_total in [10, 50, 100]:
                            await helpers.safe_send_dm(message.author, f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")
        await bot.process_commands(message)

    @bot.event
    async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
        if user.bot:
            return
        message = reaction.message
        reactor_id = str(user.id)
        author = message.author
        author_id = str(author.id)
        if reactor_id == author_id:
            return
        if await database.has_reaction_been_counted(database.db_pool, message.id, reactor_id):
            return
        await database.set_reaction_counted(database.db_pool, message.id, reactor_id)
        new_total = await database.add_points(database.db_pool, author_id, 2)
        if new_total in [10, 50, 100]:
            await helpers.safe_send_dm(author, f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")

