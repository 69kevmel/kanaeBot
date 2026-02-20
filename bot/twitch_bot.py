import logging
import time
from twitchio.ext import commands
from . import config, database

logger = logging.getLogger(__name__)

# Petit dico pour éviter le spam : 1 point toutes les 60 secondes max par viewer
twitch_cooldowns = {}

class KanaeTwitchBot(commands.Bot):
    def __init__(self):
        # On initialise la connexion à ta chaîne
        super().__init__(
            token=config.TWITCH_TOKEN,
            prefix='!',
            initial_channels=[config.TWITCH_CHANNEL]
        )

    async def event_ready(self):
        logger.info(f'🎥 Bot Twitch connecté avec succès sur la chaîne : {config.TWITCH_CHANNEL}')

    async def event_message(self, message):
        # On ignore les messages du bot lui-même
        if message.echo:
            return

        twitch_user = message.author.name.lower()
        now = time.time()

        # Anti-spam : on vérifie si le mec a déjà eu des points il y a moins de 60 secondes
        if twitch_user in twitch_cooldowns and now - twitch_cooldowns[twitch_user] < 60:
            return

        # On attend que la DB soit prête
        if database.db_pool is None:
            return

        # On regarde si ce pseudo Twitch est relié à un compte Kanaé
        discord_id = await database.get_discord_by_twitch(database.db_pool, twitch_user)
        
        if discord_id:
            # Bingo ! On lui donne 1 point sur Discord
            await database.add_points(database.db_pool, discord_id, 1)
            twitch_cooldowns[twitch_user] = now
            logger.info(f"✨ +1 point Discord pour {twitch_user} via le chat Twitch !")

# On crée l'instance prête à être lancée
twitch_bot_instance = KanaeTwitchBot()