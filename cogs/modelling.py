import discord
from discord.ext import commands
from discord import app_commands

# Import your existing RiskEngine
from quant_engine import RiskEngine, QuantConfig

class ModellingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = QuantConfig()
        # Simulating your $400,000 paper trading account
        self.risk_engine = RiskEngine(account_equity=400000.0, config=self.config)

    @app_commands.command(name="position_size", description="Calculate strict institutional position sizing.")
    @app_commands.describe(entry="Trade Entry Price", stop="Stop Loss Price")
    async def position_size(self, interaction: discord.Interaction, entry: float, stop: float):
        try:
            shares = self.risk_engine.size_position(entry_price=entry, stop_loss_price=stop)
            risk_per_share = abs(entry - stop)
            total_risk = shares * risk_per_share
            total_notional = shares * entry

            embed = discord.Embed(
                title="⚖️ Risk Management Circuit Breaker",
                color=discord.Color.blue()
            )
            embed.add_field(name="Target Shares", value=f"**{shares}**", inline=False)
            embed.add_field(name="Entry Price", value=f"${entry:.2f}", inline=True)
            embed.add_field(name="Stop Loss", value=f"${stop:.2f}", inline=True)
            embed.add_field(name="Risk per Share", value=f"${risk_per_share:.2f}", inline=True)
            embed.add_field(name="Total Capital at Risk", value=f"${total_risk:,.2f} (Max 1%)", inline=True)
            embed.add_field(name="Total Notional Exposure", value=f"${total_notional:,.2f} (Max 5%)", inline=True)

            await interaction.response.send_message(embed=embed)

        except ValueError as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ModellingCog(bot))