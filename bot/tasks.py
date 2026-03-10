import asyncio
import logging
from datetime import datetime, date, timezone, timedelta
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
    
    # 15h20 UTC correspond à 16h20 en France (heure d'hiver).
    if now.hour == 15 and now.minute == 20 and now.date().toordinal() % 2 == 0:
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            return
        
        guild = channel.guild
        
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # On récupère le classement du mois en cours !
                await cur.execute(
                    "SELECT user_id, points FROM monthly_scores ORDER BY points DESC;"
                )
                all_rows = await cur.fetchall()
        
        top_filtered = []
        for uid, pts in all_rows:
            member = guild.get_member(int(uid))
            if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
                continue
            if pts > 0: # On affiche que ceux qui ont des points
                top_filtered.append((uid, pts))
            if len(top_filtered) >= 5:
                break
        
        if not top_filtered:
            return

        # Construction du message texte super stylé
        places = [
            "🥇 **1ʳᵉ place :** {name} — **{pts} pts** 🔥👑",
            "🥈 **2ᵉ place :** {name} — **{pts} pts** 💨🎖️",
            "🥉 **3ᵉ place :** {name} — **{pts} pts** 🌿",
            "🏅 **4ᵉ place :** {name} — **{pts} pts** ✨",
            "🏅 **5ᵉ place :** {name} — **{pts} pts** ✨",
        ]

        lines = [
            "🌟 **POINT CLASSEMENT : LE KANAÉ D'OR** 🌟\n",
            "Yo l'équipe ! 🌿 Petit check-up du classement actuel pour le grand concours du mois.",
            "Rien n'est joué, la course au Kanaé d'Or et au fameux cadeau mensuel bat son plein ! 🎁💨\n",
            "**Voici les 5 plus gros fumeurs du moment :**\n"
        ]

        for i, (user_id, points) in enumerate(top_filtered, 1):
            user = await bot.fetch_user(int(user_id))
            # On utilise user.mention pour que ça fasse le @Pseudo bleu !
            lines.append(places[i - 1].format(name=user.mention, pts=points))

        # Remplir les places vides si moins de 5 joueurs ont des points
        for i in range(len(top_filtered) + 1, 6):
            lines.append(places[i - 1].format(name="-", pts="-"))

        lines.append("\nRespect à vous les boss du Top 5, vous envoyez du très lourd ! 🙌")
        lines.append("Mais attention, le mois n'est pas terminé... Tout peut encore basculer !")
        lines.append("*(Tu veux voler la première place et rafler le cadeau ? Clique sur le bouton en bas pour voir comment booster tes points !)* 👇\n")
        lines.append("Restez chill, partagez la vibe. Kanaé représente ! 🌿🛋️🌈")

        msg = "\n".join(lines)
        
        # On attache ta vue avec le bouton
        view = ConcoursHelpView()

        await channel.send(content=msg, view=view)
        logger.info("Recap des 2 jours envoyé avec le bouton d'aide.")

@tasks.loop(minutes=1)
async def daily_scores_backup(bot: discord.Client):
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute == 0:
        channel = bot.get_channel(config.MOD_LOG_CHANNEL_ID)
        if not channel:
            return
            
        filename = "scores_backup.txt"
        with open(filename, "w") as f:
            f.write("--- SCORES A VIE ---\n")
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT user_id, points FROM scores;")
                    for user_id, points in await cur.fetchall():
                        f.write(f"{user_id},{points}\n")
                        
            f.write("\n--- SCORES DU MOIS ---\n")
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT user_id, points FROM monthly_scores;")
                    for user_id, points in await cur.fetchall():
                        f.write(f"{user_id},{points}\n")

        try:
            await channel.send("🗂️ **Voici le fichier de sauvegarde des DEUX scores :**", file=discord.File(filename))
            logger.info("Score backup uploaded (Vie + Mois)")
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
                    # 🌿 On passe à 15 points toutes les 30 min !
                    state.voice_times[user_id] -= 1800
                    await database.add_points(database.db_pool, user_id, 15)

class NewsApprovalView(discord.ui.View):
    def __init__(self, news_content: str):
        super().__init__(timeout=None)
        self.news_content = news_content

    @discord.ui.button(label="Publier ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # On récupère le salon public des news
        channel = interaction.client.get_channel(config.NEWS_CHANNEL_ID)
        if channel:
            # On envoie la news aux joueurs
            await channel.send(self.news_content)
            
            # On désactive les boutons et on met à jour le message staff
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content=f"✅ **Validé et publié par {interaction.user.mention}**\n\n{self.news_content}", view=self)
        else:
            await interaction.response.send_message("❌ Impossible de trouver le salon public des news. Vérifie NEWS_CHANNEL_ID.", ephemeral=True)

    @discord.ui.button(label="Rejeter ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        # On désactive les boutons et on marque la news comme rejetée
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"❌ **Rejeté par {interaction.user.mention}**\n\n{self.news_content}", view=self)

@tasks.loop(hours=2)
async def fetch_and_send_news(bot: discord.Client):
    logger.info("🚀 Tâche fetch_and_send_news démarrée (cycle de 2 heures)")
    await bot.wait_until_ready()

    while database.db_pool is None:
        await asyncio.sleep(1)

    # 🛑 ON CIBLE MAINTENANT LE SALON STAFF 🛑
    review_channel = bot.get_channel(config.STAFF_NEWS_REVIEW_CHANNEL_ID)
    if not review_channel:
        logger.warning("❗ Canal de review staff introuvable.")
        return

    logger.info("🔍 Récupération des flux RSS...")
    today = date.today()
    socket.setdefaulttimeout(10)

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

        message_content = (
            f"🌿 **Nouvelles fraîches de la journée !** 🌿\n"
            f"**{title}**\n"
            f"{link}\n\n"
            f"🗓️ Publié le : {published_date}"
        )

        # 🛑 ENVOI AU STAFF AVEC LES BOUTONS DE VALIDATION 🛑
        view = NewsApprovalView(message_content)
        await review_channel.send(f"📰 **NOUVELLE NEWS À VALIDER** 📰\n\n{message_content}", view=view)
        
        # On marque la news comme "traitée" dans la DB pour éviter qu'elle ne revienne à la prochaine boucle (qu'elle soit acceptée ou refusée)
        await database.mark_news_sent(database.db_pool, link, today)
        await asyncio.sleep(2)

    logger.info("✅ %d news envoyées en validation staff", len(all_entries))


async def spawn_pokeweed_loop(bot: discord.Client):
    await bot.wait_until_ready()
    logger.info("🌱 Boucle de spawn Pokéweed démarrée !")

    while True:
        # Délai entre 4h et 5h
        delay = random.randint(20000, 25000)
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

class ConcoursHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Comment gagner plus de points ? 💡", style=discord.ButtonStyle.success, custom_id="help_concours_btn_weekly")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = (
            "🏆 **GUIDE DU CONCOURS KANAÉ D'OR** 🏆\n\n"
            "💸 **Soutien & Croissance (Le Jackpot)**\n"
            "   • 💎 **Boost Discord :** +1000 points instantanés pour le soutien !\n"
            "   • 💜 **Twitch Sub :** +1000 points / mois (via `/refresh-points`)\n"
            "   • 🔗 **Twitch Follow :** +200 points (1 seule fois, via `/refresh-points`)\n"
            "   • 🤝 **Parrainage :** +250 points si ton invité reste au moins 2 heures\n\n"
            "🎰 **Économie & Casino**\n"
            "   • 🌅 **`/wakeandbake` :** +50 points par jour (jusqu'à 100 pts si tu as une bonne série) !\n"
            "   • 🎲 **`/bet` & `/douille` :** Multiplie tes points en jouant... ou perds tout !\n\n"
            "🗣️ **Activité Discord (Grind Quotidien)**\n"
            "   • 🎙️ **Vocal :** +15 points toutes les 30 minutes passées en salon vocal\n"
            "   • 📸 **Médias :** +15 points par photo/vidéo postée (1 fois par jour et par salon spécial)\n"
            "   • ✨ **Réactions :** +2 points par émoji reçu sur tes messages\n\n"
            "🧵 **Le Forum (Threads)**\n"
            "   • 📝 **Créer un sujet :** +25 points (1 fois/jour)\n"
            "   • 💬 **Participer :** +5 points pour ta première réponse sur un sujet\n"
            "   • 👑 **Bonus Créateur :** +2 points quand quelqu'un te répond\n\n"
            "📺 **Activité Twitch**\n"
            "   • 💬 **Chat en live :** +1 point par message envoyé quand le live est ON (1 pt/minute max)\n\n"
            "🌿 **Mini-Jeu Pokéweed**\n"
            "   • 🃏 **`/booster` :** +2 à +15 points par carte (+5 pts si c'est une nouvelle !)\n"
            "   • ⚡ **`/capture` :** Gagne des points bonus si tu es le premier à attraper le sauvage\n\n"
            "🔥 *Que le meilleur gagne frérot !*"
        )
        await interaction.response.send_message(message, ephemeral=True)

@tasks.loop(minutes=1)
async def wake_and_bake_reminder(bot: discord.Client):
    now = datetime.now(timezone.utc)
    
    # 20h00 UTC = exactement 4h avant le reset de minuit UTC
    if now.hour == 20 and now.minute == 0:
        logger.info("⏰ Lancement des rappels Wake & Bake...")
        today = now.date()
        yesterday = today - timedelta(days=1)
        
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, streak FROM wake_and_bake WHERE last_claim = %s AND streak >= 1;", 
                    (yesterday,)
                )
                users_at_risk = await cur.fetchall()
        
        for user_id, streak in users_at_risk:
            try:
                user = await bot.fetch_user(int(user_id))
                if user:
                    msg = (
                        f"🚨 **ALERTE WAKEANDBAKE FRÉROT !** 🚨\n\n"
                        f"Il te reste moins de **4 heures** pour faire ton `/wakeandbake` aujourd'hui !\n"
                        f"Si tu ne le fais pas, tu vas perdre ta série actuelle de **{streak} jours** 🔥 et ton multiplicateur retombera à zéro.\n\n"
                        f"Fonce sur le serveur sauver ton bonus ! 💨"
                    )
                    await helpers.safe_send_dm(user, msg)
                
                # 🛑 LA SÉCURITÉ ANTI-BAN DISCORD EST ICI 🛑
                # Le bot attend 2 secondes avant d'envoyer le prochain message.
                # Si tu as 30 joueurs à prévenir, ça prendra 1 minute, ce qui est très "safe" pour Discord.
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.warning(f"Impossible d'envoyer le rappel W&B à {user_id}: {e}")

@tasks.loop(minutes=1)
async def monthly_winner_announcement(bot: discord.Client):
    now = datetime.now(timezone.utc)
    
    # S'exécute le 1er de chaque mois, à 16h20 pile
    if now.day == 1 and now.hour == 10 and now.minute == 0:
        channel = bot.get_channel(config.HALL_OF_FLAMME_CHANNEL_ID)
        if not channel:
            return
            
        guild = channel.guild
        
        # Récupérer les scores DU MOIS uniquement
        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT user_id, points FROM monthly_scores ORDER BY points DESC;")
                all_rows = await cur.fetchall()
                
        # Filtrer les exclus (rôles ignorés, admins...)
        top_filtered = []
        for uid, pts in all_rows:
            member = guild.get_member(int(uid))
            if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
                continue
            top_filtered.append((uid, pts))
                
        if not top_filtered:
            # Si personne n'a joué, on remet juste à zéro
            await database.reset_monthly_scores(database.db_pool)
            return

        # Le vainqueur
        winner_id, winner_pts = top_filtered[0]
        winner = guild.get_member(int(winner_id)) or await bot.fetch_user(int(winner_id))
        
        # Le texte stylé
        msg = (
            f"🌟 **RÉSULTAT DU CONCOURS DU MOIS** 🌟\n\n"
            f"Il est maintenant temps de désigner le gagnant du concours 🪙│kanaé･d･or│🪙\n\n"
            f"Le gagnant est {winner.mention} qui est donc le nouveau 🪙│kanaé･d･or│🪙 du mois avec plus de **{winner_pts} points** ! 🏆\n\n"
            f"Les points vont être réinitialisés pour le nouveau mois, et donc un nouveau cadeau sera mis en jeu 🎁\n\n"
            f"La team Kanaé 💚\n\n"
            f"<@&{config.ROLE_MEMBRE_ID}>"
        )
        
        # On envoie le message texte
        await channel.send(content=msg)
        
        # Remise à zéro mensuelle
        await database.reset_monthly_scores(database.db_pool)
        logger.info("Annonce mensuelle envoyée et scores du mois remis à zéro.")

@tasks.loop(minutes=1)
async def daily_staff_briefing(bot: discord.Client):
    now = datetime.now(timezone.utc)
    
    # Exécution à 09h00 UTC (10h00 heure d'hiver, 11h00 heure d'été en France)
    if now.hour == 9 and now.minute == 0:
        channel = bot.get_channel(config.STAFF_NEWS_REVIEW_CHANNEL_ID) # 👈 METS LE BON ID DANS TON CONFIG.PY
        if not channel:
            return

        async with database.db_pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Événements d'AUJOURD'HUI
                await cur.execute("SELECT heure, animateur_id, titre FROM planning_pro WHERE slot_date = CURDATE() AND est_reserve = TRUE ORDER BY heure ASC;")
                events_today = await cur.fetchall()
                
                # 2. Événements des 7 PROCHAINS JOURS
                await cur.execute("SELECT slot_date, heure, animateur_id, titre FROM planning_pro WHERE slot_date > CURDATE() AND slot_date <= DATE_ADD(CURDATE(), INTERVAL 7 DAY) AND est_reserve = TRUE ORDER BY slot_date ASC;")
                events_week = await cur.fetchall()
                
                # 3. Créneaux LIBRES dans les 7 PROCHAINS JOURS
                await cur.execute("SELECT slot_date, heure FROM planning_pro WHERE slot_date >= CURDATE() AND slot_date <= DATE_ADD(CURDATE(), INTERVAL 7 DAY) AND est_reserve = FALSE ORDER BY slot_date ASC;")
                free_slots = await cur.fetchall()

        # Construction du message
        lines = ["☀️ **BRIEFING STAFF DU JOUR !** ☀️\n"]

        if events_today:
            lines.append("🔥 **AU PROGRAMME AUJOURD'HUI :**")
            for heure, anim_id, titre in events_today:
                lines.append(f"⏰ **{heure}** : {titre} (par <@{anim_id}>)")
        else:
            lines.append("💤 **AUJOURD'HUI :** Aucun event de prévu. Journée chill !")

        lines.append("\n📅 **DANS LES 7 PROCHAINS JOURS :**")
        if events_week:
            for d, heure, anim_id, titre in events_week:
                date_str = d.strftime("%d/%m")
                lines.append(f"• Le **{date_str}** à {heure} : {titre}")
        else:
            lines.append("• *Rien de prévu cette semaine pour le moment.*")

        lines.append("\n⚠️ **CRÉNEAUX À PRENDRE :**")
        if free_slots:
            lines.append(f"Il reste **{len(free_slots)} créneaux libres** dans les prochains jours ! Ne dormez pas dessus l'équipe :")
            for d, heure in free_slots:
                lines.append(f"🟢 **{d.strftime('%d/%m')}** à {heure}")
            lines.append("\n👉 *Utilisez `/reserver` pour poser votre animation !*")
        else:
            lines.append("Tous les créneaux ouverts sont pris ! Bon boulot la team. 👏")

        embed = discord.Embed(description="\n".join(lines), color=discord.Color.gold())
        await channel.send(embed=embed)

@tasks.loop(hours=1)
async def auto_refresh_planning(bot: discord.Client):
    """Vérifie l'affichage toutes les heures pour supprimer les jours passés."""
    await bot.wait_until_ready()
    await helpers.refresh_event_message(bot)

class QuizView(discord.ui.View):
    def __init__(self, question_data):
        super().__init__(timeout=3600) # Le quiz expire au bout d'1 heure
        self.q_data = question_data
        self.answered = False
        self.wrong_users = set()

        # On crée les 4 boutons avec les options
        for i, opt in enumerate(question_data["options"]):
            btn = discord.ui.Button(label=opt, style=discord.ButtonStyle.primary, custom_id=f"quiz_opt_{i}")
            btn.callback = self.make_callback(i)
            self.add_item(btn)

    def make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            if self.answered:
                await interaction.response.send_message("⏳ Trop tard, quelqu'un a déjà trouvé la bonne réponse !", ephemeral=True)
                return
                
            if interaction.user.id in self.wrong_users:
                await interaction.response.send_message("❌ Tu as déjà répondu faux ! Laisse les autres essayer.", ephemeral=True)
                return

            if index == self.q_data["answer"]:
                self.answered = True
                pts_win = 50
                
                # 🏆 Il a gagné ! On donne les points
                await database.add_points(database.db_pool, str(interaction.user.id), pts_win)
                
                # On désactive et on colorie les boutons
                for i, child in enumerate(self.children):
                    child.disabled = True
                    if i == self.q_data["answer"]:
                        child.style = discord.ButtonStyle.success
                    else:
                        child.style = discord.ButtonStyle.secondary
                        
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.description += f"\n\n🎉 **BINGO !** {interaction.user.mention} a trouvé la bonne réponse et rafle **+{pts_win} points** !"
                
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                # ❌ Il s'est trompé ! On retire des points
                self.wrong_users.add(interaction.user.id)
                pts_loss = 10
                await database.add_points(database.db_pool, str(interaction.user.id), -pts_loss)
                await interaction.response.send_message(f"❌ Faux ! C'est pas ça frérot. Tu perds **{pts_loss} points**. Kof Kof...", ephemeral=True)
                
        return callback

    async def on_timeout(self):
        # Si personne ne trouve après 1h, on désactive les boutons
        if not self.answered and hasattr(self, 'message'):
            for child in self.children:
                child.disabled = True
            try:
                embed = self.message.embeds[0]
                embed.color = discord.Color.light_grey()
                embed.description += "\n\n⏰ *Temps écoulé ! Personne n'a eu la bonne réponse...*"
                await self.message.edit(embed=embed, view=self)
            except:
                pass


async def trigger_quiz(bot: discord.Client, forced_channel=None):
    from .quiz_data import QUIZ_QUESTIONS
    import random
    
    if forced_channel:
        channel = forced_channel
    else:
        # On définit les salons où le bot a le droit de poser ses questions aléatoires
        # (Pour éviter qu'il spam le salon Règles ou Annonces)
        possible_channels = [
            config.BLABLA_CHANNEL_ID,
            # Tu peux ajouter d'autres salons ici, ex: config.CHANNEL_POKEWEED_ID
        ]
        channel = bot.get_channel(random.choice(possible_channels))
    
    if not channel:
        return
        
    q_data = random.choice(QUIZ_QUESTIONS)
    
    embed = discord.Embed(
        title=f"🧠 LE QUIZ ENFUMÉ - {q_data.get('category', 'Culture G')}",
        description=f"**{q_data['question']}**\n\n*Le premier à cliquer sur la bonne réponse gagne 50 points ! (Attention : -10 pts si tu te trompes !)*",
        color=discord.Color.orange()
    )
    embed.set_thumbnail(url="https://i.imgur.com/uR1a34G.gif") # Un petit gif stylé (le même que le LG)
    
    view = QuizView(q_data)
    msg = await channel.send(embed=embed, view=view)
    view.message = msg # On sauvegarde le message pour le timeout


async def random_quiz_loop(bot: discord.Client):
    """Fait pop un quiz aléatoirement entre 3h et 6h."""
    await bot.wait_until_ready()
    logger.info("🧠 Boucle de Quiz démarrée !")

    while True:
        # Tirage aléatoire entre 3h (10800 sec) et 6h (21600 sec)
        import random
        delay = random.randint(3 * 3600, 6 * 3600)
        logger.info(f"⏳ Prochain Quiz dans {delay // 3600}h et {(delay % 3600)//60}m.")
        
        try:
            await asyncio.sleep(delay)
            await trigger_quiz(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"❌ Erreur boucle quiz : {e}")
            await asyncio.sleep(60)