import os

# STRICT LIMITS: Prevent OpenBLAS from allocating massive memory pools
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(override=True)
TOKEN = os.getenv('DISCORD_BOT_TOKEN') 

class QuantPlatform(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='!',
            intents=discord.Intents.all(),
            help_command=None
        )

    async def setup_hook(self):
        # Load all modular command files from the /cogs folder
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                await self.load_extension(f'cogs.{filename[:-3]}')
        
        # Sync slash commands (/) to your Discord server
        await self.tree.sync()
        print("✅ Institutional Quant Platform Online and Synced.")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ CRITICAL ERROR: DISCORD_BOT_TOKEN missing from .env")
    else:
        bot = QuantPlatform()
        bot.run(TOKEN)