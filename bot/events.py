import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from . import config, database, helpers, state, tasks

logger = logging.getLogger(__name__)

class WelcomeSuiteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Le bouton ne désactive jamais

    @discord.ui.button(label="Ici la suite ! 👀", style=discord.ButtonStyle.success, custom_id="welcome_suite_btn")
    async def suite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Le texte éphémère qui explique tout
        suite_text = (
            "🍃**Bienvenue sur Kanaé !**\n"
            "Kanaé, c'est le serveur pour chiller et fumer avec la commu'.\n\n"
            "🏆 **LE KANAÉ D'OR (Le grand concours) :**\n"
            "Ici, ton activité te rapporte des points ! Parle en vocal, poste des photos de tes plus belles battes, ou joue au casino pour grimper en grade. Le but ? Devenir l'Empereur de Kanaé et rafler le Kanaé d'Or !\n\n"
            "• `/help-concours` : Guide complet des différentes façons de gagner des points 📚\n\n"
            "🎮 **LES COMMANDES DE BASE :**\n"
            "• `/score` : Voir ton nombre de points.\n"
            "• `/wakeandbake` : Ton cadeau quotidien (à faire tous les jours pour ton bonus !).\n"
            "• `/booster` : Ouvre un paquet de cartes Pokéweed (1 fois toutes les 12h).\n"
            "• `/bet` & `/douille` : Le coin casino pour miser tes points.\n"
            "• `/help-commandes` : Pour voir tout le reste de mes capacités !\n\n"
            f"🎭 **CHOISIS TON CAMP :**\n"
            f"Passe faire un tour dans <#{config.REACTION_ROLE_CHANNEL_ID}> pour afficher tes préférences (**Team Weed**, **Team Shit** ou les deux !) et débloquer ton rôle exclusif. 💨\n\n"
            f"📜 **Dernier truc :** N'oublie pas de jeter un œil aux règles dans <#{config.CHANNEL_REGLES_ID}> pour que tout se passe bien. Bonne fumette ! 💨"
        )
        await interaction.response.send_message(suite_text, ephemeral=True)

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


class LeaveSurveyView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def send_ack(self, interaction: discord.Interaction, reason: str):
        channel = self.bot.get_channel(config.MOD_LOG_CHANNEL_ID)
        if channel:
            await channel.send(f"❌ {interaction.user} a quitté le serveur : {reason}")
        await interaction.response.send_message("Merci pour ton retour !", ephemeral=True)

    @discord.ui.button(
        label="Car il n'y a pas assez de chose à faire",
        style=discord.ButtonStyle.secondary,
        custom_id="leave_reason_1",
    )
    async def reason_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ack(interaction, "Pas assez de choses à faire")

    @discord.ui.button(
        label="Car il n'y a pas assez de gens",
        style=discord.ButtonStyle.secondary,
        custom_id="leave_reason_2",
    )
    async def reason_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ack(interaction, "Pas assez de gens")

    @discord.ui.button(
        label="Pcq c'est pas mon mood de serveur",
        style=discord.ButtonStyle.secondary,
        custom_id="leave_reason_3",
    )
    async def reason_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ack(interaction, "Pas son mood")

    @discord.ui.button(
        label="Car je pensais pouvoir acheter de la verte ou du marron ?",
        style=discord.ButtonStyle.secondary,
        custom_id="leave_reason_4",
    )
    async def reason_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.send_ack(interaction, "Pensait pouvoir acheter")


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
        tasks.monthly_winner_announcement.start(bot)
        tasks.daily_scores_backup.start(bot)
        tasks.update_voice_points.start(bot)
        tasks.fetch_and_send_news.start(bot)
        tasks.wake_and_bake_reminder.start(bot)
        bot.loop.create_task(tasks.spawn_pokeweed_loop(bot))
        # Lancement du bot Twitch en tâche de fond
        from .twitch_bot import twitch_bot_instance
        bot.loop.create_task(twitch_bot_instance.start())

    @bot.event
    async def on_member_update(before: discord.Member, after: discord.Member):
        # --- 1. QUAND UN MEMBRE BOOST LE SERVEUR ---
        if not before.premium_since and after.premium_since:
            try:
                # 🎁 On donne les 1000 points direct ET on récupère le nouveau total !
                new_total = await database.add_points(database.db_pool, str(after.id), 1000)
                
                # 👑 On vérifie s'il monte en grade grâce au boost !
                await helpers.update_member_prestige_role(after, new_total)
                
                channel = after.guild.get_channel(config.BLABLA_CHANNEL_ID)
                if channel:
                    await channel.send(
                        f"💎 **{after.mention} vient de booster le serveur !**\n"
                        f"Merci infiniment pour ton soutien frérot, t'es un vrai 🔥🔥🔥\n"
                        f"🎁 Boom ! **+1000 points** Kanaé offerts au boss pour le soutien !"
                    )
            except Exception as e:
                logger.warning("❌ Erreur lors du message de boost : %s", e)
                
        # --- 2. QUAND LE BOOST D'UN MEMBRE EXPIRE ---
        elif before.premium_since and not after.premium_since:
            # Étape A : Le DM Privé
            try:
                msg_dm = (
                    "💔 **Ton boost Kanaé vient d'expirer !**\n\n"
                    "Déjà, un énorme **MERCI** pour ton soutien jusqu'ici, c'est grâce à des mecs comme toi que Kanaé tourne bien ! 🌿\n\n"
                    "🛡️ **Le tag [KNAÉ] a besoin de toi !**\n"
                    "Si tu as encore la possibilité de nous soutenir, n'hésite pas à remettre ton boost. Ça nous aide énormément à garder le tag actif pour tout le monde !\n\n"
                    "🎁 *(Et n'oublie pas : chaque nouveau boost te rapporte instantanément **+1000 points** sur ton Score Kanaé !)*\n\n"
                    "Quoi qu'il arrive, reste chill et bonne fumette ! 💨"
                )
                await helpers.safe_send_dm(after, msg_dm)
                logger.info(f"DM de fin de boost envoyé à {after.name}")
            except Exception as e:
                logger.warning("❌ Erreur lors de l'envoi du DM de fin de boost à %s : %s", after.name, e)
                
            # Étape B : L'annonce triste dans le Casino
            try:
                casino_channel = after.guild.get_channel(1477651520878280914)
                if casino_channel:
                    await casino_channel.send(
                        f"📉 **COUP DUR POUR LE SERVEUR...**\n"
                        f"Le boost de {after.mention} vient d'expirer ! 💔\n"
                        f"Merci beaucoup pour ton aide précieuse frérot, on espère que tu reviendras parmi les **Pilliers du cercle** ! 🌿🛡️"
                    )
            except Exception as e:
                logger.warning("❌ Erreur lors de l'annonce publique de fin de boost : %s", e)
    
        
    @bot.event
    async def on_member_join(member: discord.Member):
        try:
            role_membre = member.guild.get_role(config.ROLE_MEMBRE_ID)
            if role_membre:
                await member.add_roles(role_membre, reason="Nouveau membre dans le cercle Kanaé")
                logger.info("Rôle Membre du cercle donné à %s", member.name)
            else:
                logger.warning("❌ Rôle Membre du cercle introuvable. Vérifie ROLE_MEMBRE_ID.")
        except discord.Forbidden:
            logger.error("⛔ Je n'ai pas la permission de donner le rôle. Mon rôle de Bot doit être placé AU-DESSUS du rôle Membre dans les paramètres Discord !")
        except Exception as e:
            logger.warning("Erreur lors de l'attribution du rôle à %s : %s", member.name, e)
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
                        # 🌿 On passe le parrainage à 250 points !
                        new_total = await database.add_points(database.db_pool, inviter_id, 250)
                        await helpers.safe_send_dm(inviter,
                            f"🎉 Bravo frérot ! +250 points pour ton parrainage de `{member.name}`, "
                            f"il est resté 2 h sur le serveur ! Total : {new_total} points. Continue comme ça 🚀")
                asyncio.create_task(award_after_2h())
        except Exception as e:
            logger.warning("Parrainage detection failed: %s", e)

        # --- 2. MESSAGE PUBLIC DANS LE SALON BIENVENUE ---
        try:
            welcome_channel = member.guild.get_channel(config.WELCOME_CHANNEL_ID)
            
            if welcome_channel:
                blabla_mention = f"<#{config.BLABLA_CHANNEL_ID}>"
                presente_mention = f"<#{config.CHANNEL_PRESENTE_TOI_ID}>"

                welcome_text = (
                    f"🌿 Bienvenue dans la commu', {member.mention} 💨\n"
                    f"Installe-toi bien, roules-en un long et passe dire bonjour dans {blabla_mention} 🛋️\n"
                    f" ✌️ N'oublie pas de poser ta petite présentation dans {presente_mention} (c’est pas obligatoire, mais c’est le top pour faire connaissance avec les autres !) 🍃"
                )

                # On génère la vue avec le bouton "Ici la suite !" (assure-toi que WelcomeSuiteView est bien défini en haut de ton fichier)
                view = WelcomeSuiteView()

                await welcome_channel.send(content=welcome_text, view=view)
                logger.info(f"Message public de bienvenue envoyé pour {member.name}")
            else:
                logger.warning("❌ Salon de bienvenue introuvable (vérifie WELCOME_CHANNEL_ID).")
                
        except Exception as e:
            logger.warning("Échec de l'envoi du message public: %s", e)
            
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
                "🏆 **LE KANAÉ D'OR (Notre grand concours) :**\n"
                "Ici, presque tout ce que tu fais te rapporte des points (vocaux, photos, messages, casino...). "
                "Grimpe en grade, débloque des rôles de prestige et tente de devenir l'Empereur de Kanaé pour rafler le Kanaé d'Or ! 👑\n\n"
                "📦 **Le Pokéweed est là !**\n"
                "   ➕ Collectionne les 31 strains fusionnés avec des Pokémon 🌈\n"
                "   🃏 Ouvre des boosters, attrape des Pokéweeds sauvages, et complète ton Pokédex !\n"
                "   🌿 (psst... tu peux même revendre tes doublons depuis ton pokédex pour gagner des points !) 👀\n\n"
                "🎮 **LES COMMANDES À CONNAÎTRE :**\n\n"
                "🏆 **Économie & Casino :**\n"
                "   ➡️ **/wakeandbake** – Ta récompense gratuite quotidienne (fais grimper ta série !) 🌅\n"
                "   ➡️ **/score** – Voir ton score et ton rang 📊\n"
                "   ➡️ **/top-5** – Voir les 5 meilleurs fumeurs du serveur 🏆\n"
                "   ➡️ **/bet** {mise} – Parie tes points au casino (48% de chance de doubler) 🎰\n"
                "   ➡️ **/douille** {mise} – Roulette russe multijoueur (jusqu'à 6 joueurs) 🔫\n"
                "   ➡️ **/help-concours** – Guide complet des différentes façons de gagner des points 📚\n\n"
                "🌿 **Pokéweed :**\n"
                "   ➡️ **/booster** – Ouvre 4 Pokéweeds aléatoires 🔥 (1x toutes les 12h)\n"
                "   ➡️ **/capture** – Dégaine vite pour attraper le Pokéweed sauvage 💨\n"
                "   ➡️ **/pokedex** – Affiche ta collection ou vends tes doubles 🌿\n\n"
                "💜 **Réseaux & Twitch (Points gratuits) :**\n"
                "   ➡️ **/link-twitch** {pseudo} – Relie ton compte pour les récompenses de follow/sub 🎁\n"
                "   ➡️ **/mes-reseaux** – Voir la liste de tes comptes liés 🌐\n"
                "   ➡️ **/refresh-points** – Récupérer tes points Twitch 🔄\n"
                "   ➡️ **/unlink-twitch** – Délier ton compte en cas d'erreur\n\n"
                "🎵 **Musique :**\n"
                "   ➡️ **/play** {musique} – Lance une musique dans **KanaéMUSIC** 🎶\n\n"
                "🧠 **Général & Staff :**\n"
                "   ➡️ **/hey** {message} – Discute avec l'IA de **Kanaé** 🤖\n"
                "   ➡️ **/help-commandes** – Affiche ce menu d'aide complet 🛠️\n"
                "   ➡️ **/candidature** – Formulaire pour postuler et rejoindre le staff 📝\n\n"
                f"🎭 **CHOISIS TON CAMP :**\n"
                f"Passe faire un tour dans <#{config.REACTION_ROLE_CHANNEL_ID}> pour afficher tes préférences (**Team Weed**, **Team Shit** ou les deux !) et débloquer ton rôle exclusif. 💨\n\n"
            )

            await helpers.safe_send_dm(member, message)
            logger.info("Welcome DM sent to %s", member.name)
        except Exception as e:
            logger.warning("Failed to send welcome DM: %s", e)

    @bot.event
    async def on_member_remove(member: discord.Member):
        # --- 1. ENVOI DU LOG DANS LE SALON MODÉRATEUR ---
        try:
            mod_channel = bot.get_channel(config.MOD_LOG_CHANNEL_ID)
            if mod_channel:
                # 📊 Récupération de TOUTES les stats en base de données
                pts_vie = await database.get_user_points(database.db_pool, member.id)
                pts_mois = await database.get_user_monthly_points(database.db_pool, member.id)
                pokes = await database.get_user_pokeweeds_unique(database.db_pool, member.id)
                
                # Calcul du nombre total de Pokéweeds qu'il abandonne (l'index 3 correspond au "count")
                total_pokes = sum(p[3] for p in pokes) if pokes else 0

                # ⏳ Calcul du temps passé (Discord Timestamp magique)
                joined_at = int(member.joined_at.timestamp()) if member.joined_at else 0
                
                # 🎭 Détection de sa Team
                team = "Sans camp fixe 🤷‍♂️"
                if any(r.id == config.WEED_ROLE_ID for r in member.roles):
                    team = "Team Weed 🥦"
                elif any(r.id == config.SHIT_ROLE_ID for r in member.roles):
                    team = "Team Shit 🍫"

                # 📝 Petite note marrante en fonction de son activité
                if pts_vie == 0:
                    note = "Un vrai fantôme 👻. Il n'a même pas tiré une taffe avant de partir."
                elif pts_vie > 15000:
                    note = "Un PUTAIN de pilier nous quitte 🫡. On fumera le prochain en son honneur."
                elif total_pokes > 10:
                    note = "Le mec s'est barré en laissant toutes ses cartes Pokéweed à l'abandon... Triste."
                else:
                    note = "Il a fumé sa part en scred, puis il s'est taillé la route 🚶‍♂️💨."

                # 🎨 Création de l'Embed
                embed = discord.Embed(
                    title="💨 Un fumeur a écrasé son joint...",
                    description=f"**{member.name}** ({member.mention}) a déserté le cercle Kanaé.",
                    color=discord.Color.dark_red()
                )
                
                # Les infos stylées
                embed.add_field(name="📅 Arrivé sur le serveur", value=f"<t:{joined_at}:f>\n*(soit <t:{joined_at}:R>)*", inline=False)
                embed.add_field(name="🌟 Score à vie", value=f"**{pts_vie} pts**", inline=True)
                embed.add_field(name="🏆 Score du mois", value=f"**{pts_mois} pts**", inline=True)
                embed.add_field(name="🎭 Camp", value=team, inline=True)
                embed.add_field(name="🌿 Inventaire Pokéweed", value=f"Abandonne **{total_pokes}** cartes derrière lui.", inline=False)
                embed.add_field(name="📝 Bilan", value=f"*{note}*", inline=False)

                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"ID Joueur : {member.id}")

                await mod_channel.send(embed=embed)
        except Exception as e:
            logger.warning("Erreur lors de l'envoi du log de départ : %s", e)

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return

        # --- ✅ Gestion DM
        if isinstance(message.channel, discord.DMChannel):
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

        # --- ✅ Gestion messages dans TEXT CHANNELS
        if isinstance(message.channel, discord.TextChannel):
            user_id = str(message.author.id)
            channel_id = message.channel.id
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # ✅ Points pour posts avec média dans salons spéciaux
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
                        await helpers.update_member_prestige_role(message.author, new_total)

        # --- ✅ NOUVEAU : gestion des messages dans les THREADS (Forum)
        if isinstance(message.channel, discord.Thread):
            thread = message.channel
            thread_id = thread.id
            responder_id = message.author.id

            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    # Check si le mec a déjà posté dans ce thread (pour éviter multiple +5)
                    await cur.execute(
                        "SELECT 1 FROM thread_participation WHERE thread_id=%s AND user_id=%s;",
                        (thread_id, responder_id)
                    )
                    if not await cur.fetchone():
                        # Première participation ➜ +5 points
                        await cur.execute(
                            "INSERT INTO thread_participation (thread_id, user_id) VALUES (%s, %s);",
                            (thread_id, responder_id)
                        )
                        await database.add_points(database.db_pool, responder_id, 5)
                        logger.info(f"✅ +5 points à {responder_id} pour réponse dans thread {thread_id}")

                        # Bonus au créateur du thread (si ce n'est pas lui)
                        if thread.owner and thread.owner.id != responder_id:
                            await database.add_points(database.db_pool, thread.owner.id, 2)
                            logger.info(f"✅ +2 points au créateur {thread.owner.id} pour réponse de {responder_id}")

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
        await helpers.update_member_prestige_role(author, new_total)

    @bot.event
    async def on_thread_create(thread: discord.Thread):
        if thread.owner is None or thread.guild is None:
            return

        user_id = thread.owner.id
        today = datetime.now(timezone.utc).date()

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # Vérifie s'il a déjà eu son bonus aujourd'hui
                await cur.execute(
                    "SELECT 1 FROM thread_daily_creations WHERE user_id=%s AND date=%s;",
                    (user_id, today)
                )
                if await cur.fetchone():
                    logger.info(f"{user_id} a déjà créé un sujet aujourd'hui.")
                    return

                # Donne les points
                await database.add_points(database.db_pool, user_id, 25)
                new_total = await database.get_user_points(database.db_pool, user_id)
                member_obj = thread.guild.get_member(user_id)
                if member_obj:
                    await helpers.update_member_prestige_role(member_obj, new_total)
                logger.info(f"🎁 +25 points pour création de sujet par {user_id}")

                # Log la création
                await cur.execute(
                    "INSERT INTO thread_daily_creations (user_id, date) VALUES (%s, %s);",
                    (user_id, today)
                )
    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id is None:
            return
        if payload.user_id == bot.user.id:
            return

        # On cible le bon message
        target_id = config.REACTION_ROLE_MESSAGE_ID or state.weed_shit_message_id
        if not target_id or payload.message_id != target_id:
            return

        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if not member:
            return

        emoji = payload.emoji.name
        role_id = None
        if emoji == config.EMOJI_WEED:
            role_id = config.WEED_ROLE_ID
        elif emoji == config.EMOJI_SHIT:
            role_id = config.SHIT_ROLE_ID
        else:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        # Ajoute le rôle si pas déjà présent
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Reaction role add (weed/shit)")
            except discord.HTTPException:
                pass

    @bot.event
    async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.user_id is None:
            return

        target_id = config.REACTION_ROLE_MESSAGE_ID or state.weed_shit_message_id
        if not target_id or payload.message_id != target_id:
            return

        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if not member:
            return

        emoji = payload.emoji.name
        role_id = None
        if emoji == config.EMOJI_WEED:
            role_id = config.WEED_ROLE_ID
        elif emoji == config.EMOJI_SHIT:
            role_id = config.SHIT_ROLE_ID
        else:
            return

        role = guild.get_role(role_id)
        if role is None:
            return

        # Retire le rôle s'il est présent
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Reaction role remove (weed/shit)")
            except discord.HTTPException:
                pass
