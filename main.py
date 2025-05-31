import os
import json
import datetime
import random
import feedparser
import asyncio
import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

# === CONFIGURATION ===
TOKEN = os.getenv('TOKEN')  # Ton token Discord
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')  # Clé API Mistral
AGENT_ID_MISTRAL = os.getenv('AGENT_ID_MISTRAL')  # Agent ID Mistral

NEWS_CHANNEL_ID = 1377605635365011496  # Salon des news
CHANNEL_REGLES_ID = 1372288019977212017
CHANNEL_PRESENTE_TOI_ID = 1372288185299636224
CHANNEL_MONTRE_TA_BATTE_ID = 1372310203227312291

RSS_FEEDS = [
    'https://www.newsweed.fr/feed/',
    'https://lelabdubonheur.fr/blog/rss',
    'https://www.norml.fr/feed/',
]

EMOJIS = ['🔥', '💨', '🌿', '😎', '✨', '🌀', '🍁', '🎶', '🌈', '🧘']

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
sent_links = set()
user_dm_counts = {}

# === Bot Ready ===
@bot.event
async def on_ready():
    print(f"✅ KanaéBot prêt à diffuser la vibe en tant que {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Commandes slash synchronisées ({len(synced)} commandes)")
    except Exception as e:
        print(f"❗ Erreur lors de la sync des commandes : {e}")

# === MP de bienvenue ===
@bot.event
async def on_member_join(member):
    try:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="📜 Règlement", style=discord.ButtonStyle.link, url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_REGLES_ID}"))
        view.add_item(discord.ui.Button(label="🙋 Présente-toi", style=discord.ButtonStyle.link, url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_PRESENTE_TOI_ID}"))
        view.add_item(discord.ui.Button(label="🌿 Montre ta batte", style=discord.ButtonStyle.link, url=f"https://discord.com/channels/{member.guild.id}/{CHANNEL_MONTRE_TA_BATTE_ID}"))

        message = (
            f"🌿 Yo {member.name} ! Bienvenue dans le cercle **{member.guild.name}**.\n\n"
            "Ici, ça chill, ça partage, et ça kiffe. **0 pression**. 😎\n"
            "Que tu sois là pour montrer ta dernière **batte** 🌿, ton **matos** 🔥, ou juste pour papoter 💬, **t'es chez toi**.\n\n"
            "Avant de te lancer, check les règles 📜 et **présente-toi** 🙋 (Montre qui t'es, en fait).\n\n"
            "Ensuite, n'hésite pas à découvrir les autres salons et à te balader 🚀.\n\n"
            "**(👻 Discret ? Si tu veux changer ton pseudo, clique droit sur ton profil à droite et choisis 'Changer le pseudo')**\n\n"
            "Quelques commandes utiles :\n"
            "   ➡️ **/play** {nom de la musique} - Pour écouter de la musique dans le channel **KanaéMUSIC** 🎶\n"
            "   ➡️ **/hey** {message} - Pour parler avec l'**IA officielle** de **Kanaé** 🤖\n\n"
            "👉 Clique sur les boutons ci-dessous pour bien t'installer sur le serveur ! 🌿🔥"
        )


        await member.send(content=message, view=view)
        print(f"✅ MP de bienvenue envoyé à {member.name}")

    except Exception as e:
        print(f"❗ Erreur lors de l'envoi du MP : {e}")

# === Réponses aux DM ===
@bot.event
async def on_message(message):
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

    await bot.process_commands(message)

# === Commande slash /hey ===
@bot.tree.command(name="hey", description="Parle avec le bot via Mistral")
@app_commands.describe(message="Ton message à envoyer")
async def hey(interaction: discord.Interaction, message: str):
    await interaction.response.defer()

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "prompt": message,
                "agent_id": AGENT_ID_MISTRAL,
                "max_tokens": 400
            }
            async with session.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=payload) as resp:
                print(f"Erreur lors de l'appel à l'API Mistral : {resp}")
                if resp.status == 200:
                    data = await resp.json()
                    response_text = data.get("response", "Désolé, j'ai pas compris frérot'.")
                else:
                    response_text = "Votre correspondant est actuellement injoignable."
    except Exception as e:
        print(f"Erreur lors de l'appel à l'API Mistral : {e}")
        response_text = "Votre correspondant est actuellement injoignable."

    await interaction.followup.send(response_text, ephemeral=True)

    try:
        original_message = await interaction.original_response()
        await original_message.add_reaction(random.choice(EMOJIS))
    except Exception as e:
        print(f"Erreur lors de l'ajout de la réaction : {e}")

# === News : Récupération et envoi RSS ===
async def fetch_and_send_news():
    await bot.wait_until_ready()
    channel = bot.get_channel(NEWS_CHANNEL_ID)

    if not channel:
        print("❗ Channel des news introuvable.")
        return

    print(f"✅ Salon des news trouvé : {channel}")

    while True:
        now = datetime.datetime.utcnow()
        today = now.date()

        print(f"🔄 [{now.strftime('%Y-%m-%d %H:%M:%S')}] Vérification des news...")

        all_entries = []
        for feed_url in RSS_FEEDS:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                published = entry.get('published_parsed')
                if published:
                    entry_date = datetime.date(published.tm_year, published.tm_mon, published.tm_mday)
                    if entry_date == today and entry.link not in sent_links:
                        all_entries.append(entry)

        if all_entries:
            entry = random.choice(all_entries)
            sent_links.add(entry.link)

            message = (
                f"🌿 **Nouvelles fraîches de la journée !** 🌿\n"
                f"**{entry.title}**\n"
                f"{entry.link}\n\n"
                f"🗓️ Publié le : {datetime.date(entry.published_parsed.tm_year, entry.published_parsed.tm_mon, entry.published_parsed.tm_mday)}"
            )

            await channel.send(message)
            print(f"✅ News postée : {entry.title}")
        else:
            print("❗ Aucune nouvelle à publier cette fois-ci.")

        print("⏳ Attente de 3 heures avant la prochaine vérification...")
        await asyncio.sleep(3 * 3600)

# === Lancement du bot ===
async def main():
    async with bot:
        bot.loop.create_task(fetch_and_send_news())
        await bot.start(TOKEN)

asyncio.run(main())
