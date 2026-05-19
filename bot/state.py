import asyncio

# Global runtime state for the bot
voice_times = {}
user_dm_counts = {}
invite_cache = {}
current_spawn = None
capture_winner = None
weed_shit_message_id = 0

# Verrou pour éviter la double-capture simultanée (C2)
capture_lock = asyncio.Lock()