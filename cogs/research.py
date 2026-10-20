import os
import aiohttp
import xml.etree.ElementTree as ET
import discord
from discord.ext import commands
from groq import Groq
from dotenv import load_dotenv

class ResearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        load_dotenv(override=True)
        # Initialize the Groq client instead of Google
        self.ai_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    @commands.command(name="arxiv", help="Fetches and summarizes the latest Quantitative Finance paper.")
    async def fetch_arxiv(self, ctx):
        async with ctx.typing():
            try:
                url = 'http://export.arxiv.org/api/query?search_query=cat:q-fin.TR+OR+cat:q-fin.ST&sortBy=submittedDate&sortOrder=desc&max_results=1'
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        xml_data = await response.read()
                        
                root = ET.fromstring(xml_data)
                namespace = {'atom': 'http://www.w3.org/2005/Atom'}
                entry = root.find('atom:entry', namespace)
                
                if not entry:
                    await ctx.send("❌ Research Agent: Could not locate recent papers.")
                    return

                title = entry.find('atom:title', namespace).text.strip().replace('\n', ' ')
                abstract = entry.find('atom:summary', namespace).text.strip()
                paper_link = entry.find('atom:id', namespace).text

                system_prompt = (
                    "You are a senior quantitative finance researcher. "
                    "Read the following academic abstract and summarize it in exactly 3 bullet points. "
                    "Focus strictly on: 1) The mathematical/statistical model used. 2) The data or asset class tested. 3) The trading edge or conclusion."
                )
                
                def query_ai():
                    chat_completion = self.ai_client.chat.completions.create(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"Title: {title}\nAbstract: {abstract}"}
                        ],
                        # Universally unlocked on Groq's free tier/ use other API if there are any possibilites
                        model="qwen/qwen3.8-27b", 
                    )
                    return chat_completion.choices[0].message.content
                
                ai_response = await self.bot.loop.run_in_executor(None, query_ai)

                embed = discord.Embed(
                    title=f"🎓 arXiv Quant Research", 
                    description=f"**{title}**",
                    url=paper_link, 
                    color=discord.Color.dark_purple()
                )
                embed.add_field(name="AI Abstract Summary", value=ai_response, inline=False)
                embed.set_footer(text="Source: arXiv API | Category: q-fin | Engine: Llama-3")
                
                await ctx.send(embed=embed)

            except Exception as e:
                await ctx.send(f"❌ Research Agent Error: {e}")

async def setup(bot):
    await bot.add_cog(ResearchCog(bot))