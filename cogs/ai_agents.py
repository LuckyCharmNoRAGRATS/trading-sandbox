import discord
from discord.ext import commands
from discord import app_commands
import pandas as pd

# Import your data layer and AI filter
from data_fetcher import get_historical_bars
from ai.meta_labeler import MetaLabelFilter

class AIAgentsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ai_filter = MetaLabelFilter()

    async def _run_ai_logic(self, symbol: str):
        """Core AI logic separated so both slash and text commands can use it."""
        if self.ai_filter.model is None:
            return "❌ Error: ML Model not found. Run `python ai/train_model.py` first.", None
        
        # Fetch data and generate features
        bars = get_historical_bars(symbol=symbol, days=60, force_refresh=True)
        features_df = self.ai_filter.generate_features(bars)
        current_features = features_df.iloc[-1]
        confidence = self.ai_filter.get_confidence_score(current_features)
        
        # Format the AI's decision
        is_approved = confidence >= 0.60
        color = discord.Color.green() if is_approved else discord.Color.red()
        decision = "✅ APPROVED" if is_approved else "🛑 VETOED"
        
        embed = discord.Embed(title=f"🧠 AI Meta-Label Prediction: {symbol.upper()}", color=color)
        embed.add_field(name="AI Decision", value=f"**{decision}**", inline=False)
        embed.add_field(name="Probability of Profit", value=f"{confidence:.2%}", inline=True)
        embed.add_field(name="Min. Required Confidence", value="60.00%", inline=True)
        embed.add_field(
            name="Underlying Features", 
            value=f"Volatility: {current_features['volatility_20d']:.2%}\nHurst Exponent: {current_features['hurst']:.2f}", 
            inline=False
        )
        return None, embed

    # 1. The original Slash Command (will work once Discord syncs)
    @app_commands.command(name="ai_veto", description="Ask the Machine Learning model if a trade is safe.")
    async def ai_veto(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer()
        try:
            error_msg, embed = await self._run_ai_logic(symbol)
            if error_msg:
                await interaction.followup.send(error_msg)
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ AI Execution Error: {e}")

    # 2. The Instant Text Command Bypass
    @commands.command(name="ai")
    async def ai_text(self, ctx, symbol: str):
        # Shows "typing..." in Discord while calculating
        async with ctx.typing():
            try:
                error_msg, embed = await self._run_ai_logic(symbol)
                if error_msg:
                    await ctx.send(error_msg)
                else:
                    await ctx.send(embed=embed)
            except Exception as e:
                await ctx.send(f"❌ AI Execution Error: {e}")

async def setup(bot):
    await bot.add_cog(AIAgentsCog(bot))