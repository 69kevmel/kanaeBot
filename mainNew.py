import asyncio
import logging

import discord
from discord.ext import commands

from bot import config, events, commands as bot_commands, tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.dm_messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Register events and commands
events.setup(bot)
bot_commands.setup(bot)

async def main():
    async with bot:
        await bot.start(config.TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

