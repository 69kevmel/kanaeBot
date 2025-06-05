import os
import datetime
import random
import feedparser
import asyncio
import aiohttp
import discord
import aiomysql
import urllib.parse
from discord.ext import tasks, commands
from datetime import datetime, date, timedelta, timezone
from discord import app_commands

# === CONFIGURATION ===
TOKEN = os.getenv('TOKEN')  # Ton token Discord
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')  # Clé API Mistral
AGENT_ID_MISTRAL = os.getenv('AGENT_ID_MISTRAL')  # Agent ID Mistral

NEWS_CHANNEL_ID = 1377605635365011496       # Salon des news
CHANNEL_REGLES_ID = 1372288019977212017     # Salon Règlement
CHANNEL_PRESENTE_TOI_ID = 1372288185299636224  # Salon Présentation
CHANNEL_MONTRE_TA_BATTE_ID = 1372310203227312291  # Salon Montre ta batte
MOD_LOG_CHANNEL_ID = 1372328694739107880    # Salon mod-log
CONCOURS_CHANNEL_ID = 1372289319984693328   # Salon Concours
HALL_OF_FLAMME_CHANNEL_ID = CONCOURS_CHANNEL_ID  # Même salon que CONCOURS_CHANNEL_ID

RSS_FEEDS = [
    'https://www.newsweed.fr/feed/',
    'https://lelabdubonheur.fr/blog/rss',
    'https://www.norml.fr/feed/',
]

EMOJIS = ['🔥', '💨', '🌿', '😎', '✨', '🌀', '🍁', '🎶', '🌈', '🧘']

# Liste des IDs de salons où on peut gagner 15 points par photo (1 fois par jour par salon)
SPECIAL_CHANNEL_IDS = {
    1372310203227312291: 15,
    1372288717279985864: 15,
    1372310123313369169: 15,
    1379055632858091581: 15,
    1372288229750865990: 15,
    1372288825308610750: 15
}

DATABASE_URL = os.getenv('MYSQL_URL')

if DATABASE_URL:
    # Exemple de DATABASE_URL : "mysql://alice:secret123@b3ef01-foobar-1.railway.app:5432/kanaedb"
    url = urllib.parse.urlparse(DATABASE_URL)
    MYSQLUSER     = url.username                # "alice"
    MYSQLPASSWORD = url.password                # "secret123"
    MYSQLHOST     = url.hostname                # "b3ef01-foobar-1.railway.app"
    MYSQLPORT     = url.port                    # 5432 (type int)
    MYSQLDATABASE = url.path.lstrip('/')        # "kanaedb" (on enlève le "/" au début)
else:
    # Si tu exécutes en local (ou n’as pas défini MYSQL_URL),
    # on retombe sur la méthode « classique » avec plusieurs variables séparées :
    MYSQLHOST     = os.getenv('MYSQLHOST', 'localhost')
    MYSQLPORT     = int(os.getenv('MYSQLPORT', 3306))
    MYSQLUSER     = os.getenv('MYSQLUSER', 'root')
    MYSQLPASSWORD = os.getenv('MYSQLPASSWORD', '')
    MYSQLDATABASE = os.getenv('MYSQLDATABASE', 'kanaebot')

# Variables en mémoire
voice_times = {}        # { user_id: accumulated_seconds }
reaction_tracker = set()  # set of (message_id, reactor_id) pour éviter double-comptabilisation
invite_cache = {}       # { guild_id: [Invite objects] }

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.dm_messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)
sent_links = set()
user_dm_counts = {}

# === UTILITAIRES BASE DE DONNÉES ===

async def init_db_pool():
    return await aiomysql.create_pool(
        host=MYSQLHOST,
        port=MYSQLPORT,
        user=MYSQLUSER,
        password=MYSQLPASSWORD,
        db=MYSQLDATABASE,
        autocommit=True
    )

async def ensure_tables(pool):
    """
    Crée les tables nécessaires si elles n'existent pas.
    - scores(user_id BIGINT PRIMARY KEY, points INT)
    - daily_limits(user_id BIGINT, channel_id BIGINT, date DATE, PRIMARY KEY(user_id, channel_id, date))
    - reaction_tracker(message_id BIGINT, reactor_id BIGINT, PRIMARY KEY(message_id, reactor_id))
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    user_id BIGINT PRIMARY KEY,
                    points INT NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_limits (
                    user_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    PRIMARY KEY(user_id, channel_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS reaction_tracker (
                    message_id BIGINT NOT NULL,
                    reactor_id BIGINT NOT NULL,
                    PRIMARY KEY(message_id, reactor_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)

async def get_user_points(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0] if row else 0

async def add_points(pool, user_id, pts):
    """
    Ajoute pts au score de user_id (incrémente ou crée l'entrée).
    Retourne le nouveau total.
    """
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO scores (user_id, points) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE points = points + VALUES(points);
            """, (int(user_id), pts))
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0]

async def has_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM daily_limits 
                WHERE user_id=%s AND channel_id=%s AND date=%s;
            """, (int(user_id), int(channel_id), date))
            return await cur.fetchone() is not None

async def set_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT IGNORE INTO daily_limits (user_id, channel_id, date)
                VALUES (%s, %s, %s);
            """, (int(user_id), int(channel_id), date))

async def has_reaction_been_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT 1 FROM reaction_tracker 
                WHERE message_id=%s AND reactor_id=%s;
            """, (int(message_id), int(reactor_id)))
            return await cur.fetchone() is not None

async def set_reaction_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT IGNORE INTO reaction_tracker (message_id, reactor_id)
                VALUES (%s, %s);
            """, (int(message_id), int(reactor_id)))

async def get_top_n(pool, n=5):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT user_id, points FROM scores
                ORDER BY points DESC
                LIMIT %s;
            """, (n,))
            return await cur.fetchall()  # liste de tuples (user_id, points)

# On crée la variable globale pour le pool
db_pool = None

# === ÉVÉNEMENTS DU BOT ===

@bot.event
async def on_ready():
    global db_pool
    # Initialisation du pool et des tables
    try:
        db_pool = await init_db_pool()
        print("✅ [DB] Pool MySQL initialisé et connecté avec succès.")
    except Exception as e:
        print(f"❌ [DB] Erreur lors de l'initialisation du pool MySQL : {e}")
        return  # on stoppe si on n’a pas pu créer le pool

    # 2. Création / vérification des tables
    try:
        await ensure_tables(db_pool)
        print("✅ [DB] Les tables ont été vérifiées/créées avec succès.")
    except Exception as e:
        print(f"❌ [DB] Erreur lors de la création/vérification des tables : {e}")
        return


    # Remplir le cache des invites pour chaque guild
    for guild in bot.guilds:
        try:
            invite_cache[guild.id] = await guild.invites()
        except Exception as e:
            print(f"❗ Erreur lors de la récupération des invites pour {guild.name} : {e}")

    print(f"✅ KanaéBot prêt à diffuser la vibe en tant que {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Commandes slash synchronisées ({len(synced)} commandes)")
    except Exception as e:
        print(f"❗ Erreur lors de la sync des commandes : {e}")

# --- Bouton Infos Concours ---
class InfosConcoursButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="ℹ️ Infos Concours", custom_id="infos_concours"))

    @discord.ui.button(label="ℹ️ Infos Concours", style=discord.ButtonStyle.primary, custom_id="infos_concours")
    async def concours_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = (
            "🌿 **Le Concours Kanaé :**\n\n"
            "👉 **Gagne des points en postant des photos dans les salons spéciaux :**\n"
            "   • 📸 15 points par image (1 fois par jour par salon)\n\n"
            "👉 **Gagne des points en passant du temps en vocal :**\n"
            "   • 🎙️ 1 point toutes les 30 minutes\n\n"
            "👉 **Gagne des points avec les réactions :**\n"
            "   • ✨ 1 point par émoji sur ton message (1 émoji max par membre et par message)\n\n"
            "👉 **Bonus Parrainage :**\n"
            "   • 🔗 25 points quand quelqu'un rejoint via ton lien d'invitation\n\n"
            "🎯 **Les paliers à atteindre :**\n"
            "   • 🥉 10 points ➔ Bravo frérot !\n"
            "   • 🥈 50 points ➔ Respect, t'es chaud !\n"
            "   • 🏆 100 points ➔ Légende vivante !\n\n"
            "📊 **Classements chaque lundi à 15h (Top 3) et reset mensuel.**\n\n"
            "🔥 Viens chiller, poster et papoter, et deviens le **Kanaé d'Or** de la commu !"
        )
        await interaction.response.send_message(message, ephemeral=True)

# === MP de bienvenue & Parrainage ===
@bot.event
async def on_member_join(member):
    # Attribution des points d'affiliation (25 pts)
    try:
        guild = member.guild
        invites_before = invite_cache.get(guild.id, [])
        invites_after = await guild.invites()

        used_invite = None
        for invite in invites_after:
            for old_invite in invites_before:
                if invite.code == old_invite.code and invite.uses > old_invite.uses:
                    used_invite = invite
                    break
            if used_invite:
                break

        invite_cache[guild.id] = invites_after  # Mise à jour du cache

        if used_invite and used_invite.inviter:
            inviter_id = str(used_invite.inviter.id)
            new_total = await add_points(db_pool, inviter_id, 25)
            try:
                await used_invite.inviter.send(
                    f"🎉 Bravo frérot ! Tu viens de gagner **+25 points** grâce à ton lien d'invitation. Total : {new_total} points. Continue comme ça 🚀"
                )
            except:
                print(f"❗ Impossible d'envoyer le message d'affiliation à {used_invite.inviter.display_name}.")
    except Exception as e:
        print(f"❗ Erreur lors de l'attribution des points d'affiliation : {e}")

    # Envoi du MP de bienvenue
    try:
        view = InfosConcoursButton()
        view.add_item(discord.ui.Button(
            label="📜 Règlement", style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_REGLES_ID}"
        ))
        view.add_item(discord.ui.Button(
            label="🙋 Présente-toi", style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_PRESENTE_TOI_ID}"
        ))
        view.add_item(discord.ui.Button(
            label="🌿 Montre ta batte", style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_MONTRE_TA_BATTE_ID}"
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
            "   ➡️ **/score** - Pour voir **ta place** dans le concours de **Kanaé** 🎖️ (uniquement pour toi)\n"
            "   ➡️ **/top-5** - Pour voir les **5 plus gros fumeurs** du concours de **Kanaé** 🏆 (uniquement pour toi)\n\n"
            "👉 Clique sur les boutons ci-dessous pour bien t'installer sur le serveur ! 🌿🔥"
        )

        await member.send(content=message, view=view)
        print(f"✅ MP de bienvenue envoyé à {member.name}")
    except Exception as e:
        print(f"❗ Erreur lors de l'envoi du MP : {e}")

# === Réponses aux DM ===
@bot.event
async def on_message(message):
    # --- DMs ---
    if isinstance(message.channel, discord.DMChannel) and not message.author.bot:
        user_id = str(message.author.id)
        count = user_dm_counts.get(user_id, 0)

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
            user_dm_counts[user_id] = count + 1
            print(f"✉️ Réponse envoyée à {message.author.name} : {response}")
        except Exception as e:
            print(f"❗ Erreur lors de la réponse DM : {e}")

    # --- Sur le serveur --- (points par photo uniquement)
    if not message.author.bot and isinstance(message.channel, discord.TextChannel):
        user_id = str(message.author.id)
        channel_id = message.channel.id
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 15 points par image (1 fois par jour par salon) dans les salons « montre ton »
        if channel_id in SPECIAL_CHANNEL_IDS and message.attachments:
            # Vérifier qu'au moins une pièce jointe est une image
            has_image = any(
                attachment.filename.lower().endswith((
                    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"
                ))
                for attachment in message.attachments
            )
            if has_image:
                if not await has_daily_limit(db_pool, user_id, channel_id, date_str):
                    # Enregistrer la limite journalière (éviter répétition)
                    await set_daily_limit(db_pool, user_id, channel_id, date_str)
                    # Ajouter les points
                    new_total = await add_points(db_pool, user_id, SPECIAL_CHANNEL_IDS[channel_id])

                    # Vérifier palier
                    if new_total in [10, 50, 100]:
                        try:
                            await message.author.send(
                                f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀"
                            )
                        except:
                            print(f"❗ Impossible d'envoyer le message à {message.author.display_name}.")

    await bot.process_commands(message)

# === Réactions (1 point par emoji, max 1 par membre et par message) ===
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    message = reaction.message
    reactor_id = str(user.id)
    author = message.author
    author_id = str(author.id)

    # Ne pas compter si l'auteur de la réaction est l'auteur du message
    if reactor_id == author_id:
        return

    # Clé unique pour éviter double-comptabiliser une même réaction
    if await has_reaction_been_counted(db_pool, message.id, reactor_id):
        return

    # Marquer la réaction comme comptée
    await set_reaction_counted(db_pool, message.id, reactor_id)

    # Donner 1 point à l'auteur du message
    new_total = await add_points(db_pool, author_id, 1)

    # Vérifier palier
    if new_total in [10, 50, 100]:
        try:
            await author.send(f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")
        except:
            print(f"❗ Impossible d'envoyer le message à {author.display_name}.")

# === Commande slash /hey ===
@bot.tree.command(name="hey", description="Parle avec Kanaé, l'IA officielle du serveur !")
@app_commands.describe(message="Ton message à envoyer")
async def hey(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "agent_id": AGENT_ID_MISTRAL,
                "messages": [
                    {"role": "user", "content": message}
                ]
            }
            async with session.post("https://api.mistral.ai/v1/agents/completions", headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    response_text = data['choices'][0]['message']['content']
                else:
                    response_text = f"Yo, Mistral a répondu {resp.status}. J'sais pas ce qu'il veut là frérot."
    except Exception as e:
        print(f"Erreur lors de l'appel à l'API Mistral : {e}")
        response_text = "Yo, j'crois que Mistral est en PLS là, réessaye plus tard."

    await interaction.followup.send(response_text, ephemeral=True)

# === Commande slash /score ===
@bot.tree.command(name="score", description="Affiche ta place et ton score dans le classement actuel")
async def score_cmd(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    # Récupérer tous les scores et trier
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id, points FROM scores ORDER BY points DESC;")
            sorted_rows = await cur.fetchall()  # liste de tuples (user_id, points)

    position = None
    user_score = 0
    for i, (uid, pts) in enumerate(sorted_rows, 1):
        if str(uid) == user_id:
            position = i
            user_score = pts
            break

    if position:
        await interaction.response.send_message(
            f"📊 **{interaction.user.display_name}** ({user_score} pts) → Rang #{position}.", 
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"📊 **{interaction.user.display_name}**, tu n'as pas encore de points. Poste une photo ou reste en vocal pour en gagner !",
            ephemeral=True
        )

# === Commande slash /top-5 ===
@bot.tree.command(name="top-5", description="Affiche le top 5 des meilleurs fumeurs")
async def top_5(interaction: discord.Interaction):
    top_rows = await get_top_n(db_pool, n=5)
    if not top_rows:
        await interaction.response.send_message("📊 Pas encore de points enregistrés.", ephemeral=True)
        return

    msg = "🏆 **Top 5 fumeurs :**\n"
    for i, (user_id, points) in enumerate(top_rows, 1):
        user = await bot.fetch_user(int(user_id))
        msg += f"{i}. {user.display_name} ({points} pts)\n"

    await interaction.response.send_message(msg, ephemeral=True)

# === Commande slash /launch-concours ===
@bot.tree.command(name="launch-concours", description="Lance officiellement un concours")
async def launch_concours(interaction: discord.Interaction):
    channel = bot.get_channel(HALL_OF_FLAMME_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
        return

    content = (
        "🔥 **Le concours est officiellement lancé !** 🔥\n\n"
        "🔹 Postez vos photos dans les salons « montre ton ».\n"
        "🔹 Chaque semaine, on fera un top 3.\n"
        "🔹 Les paliers (10, 50, 100 points) vous rapportent des cadeaux mystères !\n"
        "🔹 Restez branchés, et surtout, kiffez !"
    )
    await channel.send(content)
    await interaction.response.send_message("✅ Concours lancé !", ephemeral=True)

# === Commande slash /présentation-concours ===
@bot.tree.command(name="présentation-concours", description="Présente les règles du concours")
async def presentation_concours(interaction: discord.Interaction):
    channel = bot.get_channel(HALL_OF_FLAMME_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
        return

    content = (
        "📜 **Présentation du Concours Kanaé :**\n\n"
        "Bienvenue à tous ! Voici les règles du jeu :\n"
        "1. **Postez une photo** dans l'un des salons « montre ton ».\n"
        "   • 15 points par jour et par salon.\n"
        "2. **Restez en vocal** pour gagner des points : 1 point toutes les 30 minutes.\n"
        "3. **Réactions** : chaque émoji laissé par un autre membre sur votre message = 1 point (1 émoji max par membre et par message).\n"
        "4. **Parrainage** : +25 points à chaque nouveau membre qui rejoint via votre lien d’invite.\n\n"
        "🏆 **Palier des points :** 10, 50, 100 → Messages de récompense en MP.\n"
        "📆 **Récap chaque lundi à 15h (Top 3) dans ce channel**.\n"
        "🔔 **Boost mental** juste avant la fin (/pre-end).\n"
        "📢 **Fin du concours** (/end-concours).\n\n"
        "Bonne chance à tous, restez chill, et amusez-vous ! 🌿😎"
    )
    await channel.send(content)
    await interaction.response.send_message("✅ Présentation du concours postée !", ephemeral=True)

# === Commande slash /pre-end ===
@bot.tree.command(name="pre-end", description="Envoie un message de boost avant la fin du concours")
async def pre_end(interaction: discord.Interaction):
    channel = bot.get_channel(HALL_OF_FLAMME_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
        return

    content = (
        "⚡ **Attention, il ne reste que quelques heures avant la fin du concours !** ⚡\n"
        "Donnez tout ce qui vous reste, postez vos meilleures vibes, et préparez-vous pour le décompte final ! 🌿🔥"
    )
    await channel.send(content)
    await interaction.response.send_message("✅ Message de pré-fin envoyé !", ephemeral=True)

# === Commande slash /end-concours ===
@bot.tree.command(name="end-concours", description="Annonce la fin du concours")
async def end_concours(interaction: discord.Interaction):
    channel = bot.get_channel(HALL_OF_FLAMME_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❗ Le channel 'hall-of-flamme' est introuvable.", ephemeral=True)
        return

    top_rows = await get_top_n(db_pool, n=3)
    content = "🏁 **Le concours est maintenant terminé !** 🏁\n\n**Résultats :**\n"
    for i, (user_id, points) in enumerate(top_rows, 1):
        user = await bot.fetch_user(int(user_id))
        content += f"{i}. {user.display_name} ({points} pts)\n"
    content += "\nFélicitations aux gagnants et merci à tous d'avoir participé ! 🎉"

    await channel.send(content)
    await interaction.response.send_message("✅ Concours terminé et résultats postés !", ephemeral=True)

# === Récap Hebdomadaire (Lundi 15h) ===
@tasks.loop(minutes=1)
async def weekly_recap():
    now = datetime.now(timezone.utc)
    # Europe/Paris est UTC+2 en juin → 15h locale = 13h UTC
    if now.weekday() == 0 and now.hour == 13 and now.minute == 0:
        channel = bot.get_channel(HALL_OF_FLAMME_CHANNEL_ID)
        if channel:
            top_rows = await get_top_n(db_pool, n=3)
            msg = "📊 **Classement hebdo (Top 3) :**\n"
            for i, (user_id, points) in enumerate(top_rows, 1):
                user = await bot.fetch_user(int(user_id))
                msg += f"{i}. {user.display_name} ({points} pts)\n"
            await channel.send(msg)

# === Sauvegarde quotidienne des scores (Minuit UTC) ===
@tasks.loop(minutes=1)
async def daily_scores_backup():
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute == 0:  # Minuit UTC
        channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
        if channel:
            # On exporte les scores dans un fichier temporaire puis on l'envoie
            filename = "scores_backup.txt"
            async with db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT user_id, points FROM scores;")
                    rows = await cur.fetchall()

            with open(filename, "w") as f:
                for user_id, points in rows:
                    f.write(f"{user_id},{points}\n")

            try:
                await channel.send("🗂️ **Voici le fichier des scores mis à jour :**", file=discord.File(filename))
                os.remove(filename)
                print("✅ Fichier des scores envoyé dans le channel mod-log.")
            except Exception as e:
                print(f"❗ Erreur lors de l'envoi du fichier des scores : {e}")

# === Mise à jour des points de voix (toutes les 5 minutes) ===
@tasks.loop(minutes=5)
async def update_voice_points():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                user_id = str(member.id)
                voice_times[user_id] = voice_times.get(user_id, 0) + 300  # +5 min (300 sec)
                if voice_times[user_id] >= 1800:  # 30 min
                    new_total = await add_points(db_pool, user_id, 1)
                    voice_times[user_id] -= 1800

                    # Palier (MP envoyé si atteint)
                    if new_total in [10, 50, 100]:
                        try:
                            await member.send(f"🎉 Bravo frérot, t'as atteint le palier des **{new_total} points** ! 🚀")
                        except:
                            print(f"❗ Impossible d'envoyer le message à {member.display_name}.")

# === News : Récupération et envoi RSS ===
async def fetch_and_send_news():
    await bot.wait_until_ready()
    channel = bot.get_channel(NEWS_CHANNEL_ID)

    if not channel:
        print("❗ Channel des news introuvable.")
        return

    print(f"✅ Salon des news trouvé : {channel}")

    while True:
        # -- Remplacement de datetime.utcnow() par datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        today = now.date()

        print(f"🔄 [{now.strftime('%Y-%m-%d %H:%M:%S')}] Vérification des news...")

        all_entries = []
        for feed_url in RSS_FEEDS:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = entry.get('published_parsed')
                if published:
                    # published.tm_year, tm_mon, tm_mday sont des ints
                    # Utiliser directement la classe date pour construire un date
                    entry_date = date(published.tm_year, published.tm_mon, published.tm_mday)
                    if entry_date == today and entry.link not in sent_links:
                        all_entries.append(entry)

        if all_entries:
            entry = random.choice(all_entries)
            sent_links.add(entry.link)

            message = (
                f"🌿 **Nouvelles fraîches de la journée !** 🌿\n"
                f"**{entry.title}**\n"
                f"{entry.link}\n\n"
                f"🗓️ Publié le : {date(entry.published_parsed.tm_year, entry.published_parsed.tm_mon, entry.published_parsed.tm_mday)}"
            )

            await channel.send(message)
            print(f"✅ News postée : {entry.title}")
        else:
            print("❗ Aucune nouvelle à publier cette fois-ci.")

        print("⏳ Attente de 3 heures avant la prochaine vérification...")
        await asyncio.sleep(3 * 3600)

# === Lancement du bot et des tâches ===
async def main():
    async with bot:
        bot.loop.create_task(fetch_and_send_news())
        daily_scores_backup.start()
        update_voice_points.start()
        weekly_recap.start()
        await bot.start(TOKEN)

asyncio.run(main())
