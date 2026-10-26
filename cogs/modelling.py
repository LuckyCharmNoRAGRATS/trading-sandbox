import discord
from discord.ext import commands
from alpaca.trading.client import TradingClient
from quant_engine import RiskEngine, QuantConfig

class ModellingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = QuantConfig()
        try:
            self.trading_client = TradingClient(self.config.api_key, self.config.secret_key, paper=True)
        except Exception:
            self.trading_client = None

    @discord.app_commands.command(name="position_size", description="Calculate institutional position size.")
    async def position_size(self, interaction: discord.Interaction, symbol: str, entry_price: float, stop_loss: float):
        if not self.trading_client:
            await interaction.response.send_message("❌ Alpaca client offline. Check API keys.")
            return
            
        try:
            account = self.trading_client.get_account()
            equity = float(account.equity)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to fetch live equity: {e}")
            return
            
        engine = RiskEngine(account_equity=equity, config=self.config)
        try:
            shares = engine.size_position(entry_price, stop_loss)
            notional = shares * entry_price
            pct_equity = (notional / equity) * 100
            
            embed = discord.Embed(title=f"Risk Engine: {symbol.upper()}", color=discord.Color.blue())
            embed.add_field(name="Account Equity", value=f"${equity:,.2f}", inline=False)
            embed.add_field(name="Target Size", value=f"**{shares} shares**", inline=True)
            embed.add_field(name="Notional Value", value=f"${notional:,.2f} ({pct_equity:.1f}%)", inline=True)
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error sizing position: {e}")

async def setup(bot):
    await bot.add_cog(ModellingCog(bot))