import logging
import time
import aiohttp
from twitchio.ext import commands
from . import config, database

logger = logging.getLogger(__name__)

# --- Variables pour l'anti-spam et le cache (éco-friendly 🌿) ---
twitch_cooldowns = {}
is_live_cache = False
last_live_check = 0

class KanaeTwitchBot(commands.Bot):
    def __init__(self):
        # On initialise la connexion à ta chaîne
        super().__init__(
            token=config.TWITCH_TOKEN,
            prefix='!',
            initial_channels=[config.TWITCH_CHANNEL]
        )

    async def check_if_live(self):
        global is_live_cache, last_live_check
        now = time.time()
        
        # 🌿 ÉCO-FRIENDLY : On vérifie l'état du live toutes les 5 minutes (300 secondes) max
        if now - last_live_check > 300:
            try:
                async with aiohttp.ClientSession() as session:
                    # Appel à DecAPI pour voir ton uptime
                    url = f"https://decapi.me/twitch/uptime/{config.TWITCH_CHANNEL}"
                    async with session.get(url) as resp:
                        text = await resp.text()
                        # Si le texte contient "offline", la chaîne est éteinte
                        is_live_cache = "offline" not in text.lower()
            except Exception as e:
                logger.error(f"Erreur check live Twitch: {e}")
                is_live_cache = False # Par sécurité, on bloque si l'API bug
            
            last_live_check = now
            logger.info(f"🔄 Check Twitch API : Le live est {'ON' if is_live_cache else 'OFF'}")
            
        return is_live_cache

    async def event_ready(self):
        logger.info(f'🎥 Bot Twitch connecté avec succès sur la chaîne : {config.TWITCH_CHANNEL}')

    async def event_message(self, message):
        # On ignore les messages du bot lui-même
        if message.echo:
            return

        # 🛑 VERIFICATION DU LIVE (Tape dans le cache la plupart du temps) 🛑
        is_live = await self.check_if_live()
        if not is_live:
            return  # Si on n'est pas en live, on stoppe tout direct !

        twitch_user = message.author.name.lower()
        now = time.time()

        # Anti-spam : on vérifie si le mec a déjà eu des points il y a moins de 60 secondes
        if twitch_user in twitch_cooldowns and now - twitch_cooldowns[twitch_user] < 60:
            return

        # On attend que la DB soit prête
        if database.db_pool is None:
            return

        # On regarde si ce pseudo Twitch est relié à un compte Kanaé
        discord_id = await database.get_discord_by_social(database.db_pool, twitch_user, "twitch")
        
        if discord_id:
            # Bingo ! On lui donne 1 point sur Discord
            await database.add_points(database.db_pool, discord_id, 1)
            twitch_cooldowns[twitch_user] = now
            logger.info(f"✨ +1 point Discord pour {twitch_user} via le chat Twitch (LIVE ON) !")

# On crée l'instance prête à être lancée
twitch_bot_instance = KanaeTwitchBot()