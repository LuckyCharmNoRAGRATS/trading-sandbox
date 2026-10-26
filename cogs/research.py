import os
import aiohttp
import xml.etree.ElementTree as ET
import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
from dotenv import load_dotenv

class ResearchAgentCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        load_dotenv(override=True)
        # Wire directly to the Groq API
        self.ai_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    @app_commands.command(name="arxiv", description="Fetch and summarize the latest quantitative finance papers.")
    async def arxiv_summary(self, interaction: discord.Interaction):
        # Defer immediately so Discord knows the bot is processing
        await interaction.response.defer()
        
        try:
            # 1. Asynchronous API Call to prevent event-loop blocking
            url = 'http://export.arxiv.org/api/query?search_query=cat:q-fin.*&sortBy=submittedDate&sortOrder=descending&max_results=1'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    xml_data = await response.read()
                    
            root = ET.fromstring(xml_data)
            namespace = {'atom': 'http://www.w3.org/2005/Atom'}
            entry = root.find('atom:entry', namespace)
            
            if not entry:
                await interaction.followup.send("❌ Research Agent: Could not locate recent papers.")
                return

            title = entry.find('atom:title', namespace).text.strip().replace('\n', ' ')
            abstract = entry.find('atom:summary', namespace).text.strip()
            paper_link = entry.find('atom:id', namespace).text

            # 2. Instruct the model to act as a quant researcher
            system_prompt = (
                "You are an elite quantitative researcher. Summarize the following academic abstract "
                "into exactly 3 concise bullet points focusing on: 1. The mathematical/statistical model, "
                "2. The data/asset class used, and 3. The trading edge or conclusion."
            )
            
            # 3. Run the synchronous Groq API call in a background thread
            def query_ai():
                chat_completion = self.ai_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Title: {title}\nAbstract: {abstract}"}
                    ],
                    # Target the massive open-source model available on your API list
                    model="openai/gpt-oss-120b", 
                )
                return chat_completion.choices[0].message.content
            
            ai_response = await self.bot.loop.run_in_executor(None, query_ai)

            # 4. Format and route the intelligence to Discord
            embed = discord.Embed(
                title=f"📚 {title}", 
                url=paper_link, 
                color=discord.Color.green()
            )
            embed.add_field(name="AI Abstract Summary", value=ai_response, inline=False)
            embed.set_footer(text="Source: arXiv API | Category: q-fin | Engine: GPT-OSS 120B")
            
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Research Agent Error: {e}")

async def setup(bot):
    await bot.add_cog(ResearchAgentCog(bot))