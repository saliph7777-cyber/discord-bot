import discord
from discord.ext import commands
from discord.ui import View, Button

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

ROLE_NAME = "אזרח"

class VerificationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="לחץ כאן לאימות מהיר", 
        style=discord.ButtonStyle.blurple, 
        custom_id="verify_button_final_v1", 
        emoji="🔓"
    )
    async def verify_callback(self, interaction: discord.Interaction, button: Button):
        role = discord.utils.get(interaction.guild.roles, name=ROLE_NAME)

        if not role:
            await interaction.response.send_message("שגיאה: לא נמצא הרול אזרח בשרת.", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("אתה כבר מאומת בשרת!", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            success_embed = discord.Embed(
                title="אימות עבר בהצלחה!",
                description="מעולה! המערכת זיהתה אותך והעניקה לך את רול האזרח בשרת.",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("אירעה שגיאה. בדוק שלבוט יש הרשאות ושעומד מעל הרול.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user.name}")

@bot.command(name="שלח_אימות")
@commands.has_permissions(administrator=True)
async def send_verification(ctx):
    try:
        await ctx.message.delete()
    except:
        pass

    embed = discord.Embed(
        title="מערכת אבטחה ואימות קהילתית",
        description="ברוך הבא לשרת! כדי להבטיח את בטחון הקהילה, עליך להשלים אימות חד-פעמי על ידי לחיצה על הכפתור למטה.",
        color=discord.Color.dark_embed()
    )
    
    embed.add_field(
        name="שלבי האימות:",
        value="1. לחץ על הכפתור למטה.\n2. תקבל את רול האזרח אוטומטית.\n3. תיפתח בפניך הגישה לכל הערוצים.",
        inline=False
    )
    
    if ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
        
    embed.set_footer(text="מערכת אימות אוטומטית מאובטחת")
    
    await ctx.send(embed=embed, view=VerificationView())

@send_verification.error
async def send_verification_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("אין לך הרשאות מנהל להשתמש בפקודה זו.", ephemeral=True)

import os
bot.run(os.environ.get('DISCORD_TOKEN'))