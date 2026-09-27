import os
import io
import json
import asyncio
import itertools
import discord
from discord.ext import commands
from discord.ui import Button, View, Select, Modal, TextInput
from dotenv import load_dotenv
from google import genai as google_genai

# ============================================================
#  טעינת כל הסודות מקובץ .env אחד משותף
# ============================================================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", "Staff")
FEEDBACK_CHANNEL_ID = int(os.getenv("FEEDBACK_CHANNEL_ID", "0") or "0")
TICKET_CATEGORY_ID = os.getenv("TICKET_CATEGORY_ID")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")

if not DISCORD_TOKEN:
    raise RuntimeError("חסר DISCORD_TOKEN בקובץ .env")

gemini_client = None
GEMINI_MODEL = "gemini-2.5-flash"
if GEMINI_API_KEY:
    gemini_client = google_genai.Client(api_key=GEMINI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

BRAND_NAME = "SkyzoneIL"
BRAND_LOGO_URL = "https://i.imgur.com/8Km9tLL.png"
DIVIDER = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "staff_stats.json")

chat_sessions: dict[int, list] = {}
ticket_counter = itertools.count(1)
active_tickets: dict[int, dict] = {}


def is_staff(member: discord.Member) -> bool:
    target = STAFF_ROLE_NAME.strip().lower()
    return any(role.name.strip().lower() == target for role in member.roles)


def branded_embed(title: str, description: str = "", color: int = 0x00C2FF) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name=f"{BRAND_NAME} | Ticket System", icon_url=BRAND_LOGO_URL)
    embed.set_footer(text=f"{BRAND_NAME} © Support System", icon_url=BRAND_LOGO_URL)
    embed.timestamp = discord.utils.utcnow()
    return embed


# ============================================================
#  1) חדר AI אישי (Gemini) - !pchat
# ============================================================
class PersonalRoomView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="מי אני", style=discord.ButtonStyle.secondary, emoji="🖥️", custom_id="who_am_i")
    async def who_am_i(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🤖 מי אני",
            description="אני עוזר ה-AI האישי של החדר הזה, מופעל על ידי **Gemini** (Google).",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="תיוג הנהלה", style=discord.ButtonStyle.danger, emoji="🔔", custom_id="ping_management")
    async def ping_management(self, interaction: discord.Interaction, button: Button):
        try:
            await interaction.channel.edit(name=f"⏳-מחכה-למענה-{interaction.user.name}")
        except Exception as e:
            print(f"שגיאה בעדכון שם החדר: {e}")
        await interaction.response.send_message("✅ ההנהלה תויגה בהצלחה ושם החדר עודכן.", ephemeral=True)

    @discord.ui.button(label="סגירת חדר אישי", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="close_room")
    async def close_room(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔒 החדר ייסגר בעוד 3 שניות...", ephemeral=True)
        chat_sessions.pop(interaction.channel.id, None)
        await asyncio.sleep(3)
        await interaction.channel.delete()


@bot.command(name="pchat")
async def create_personal_room(ctx):
    if not gemini_client:
        await ctx.send("⚠️ מפתח Gemini לא מוגדר ב-.env - פקודה זו לא זמינה.")
        return

    member = ctx.author
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
        ctx.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    channel = await ctx.guild.create_text_channel(
        name=f"💬-chat-{member.name}", overwrites=overwrites,
        topic=f"חדר שיחה אישי עבור {member.name}",
    )
    embed = discord.Embed(
        title="🤖 ברוך הבא לחדר האישי שלך",
        description=(
            f"שלום {member.mention}! זהו חדר פרטי - רק אתה וההנהלה יכולים לראות אותו.\n\n"
            "**Gemini 2.5 Flash** (Google) - אני כותב הודעה כאן ועונה בזמן אמת."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(name="🖥️ מי אני", value="מידע על העוזר החכם", inline=True)
    embed.add_field(name="🔔 תיוג הנהלה", value="קריאה לצוות התמיכה", inline=True)
    embed.add_field(name="🔒 סגירת חדר", value="סגירה מיידית ובטוחה", inline=True)
    embed.set_footer(text=f"{BRAND_NAME} • נוצר על ידי {member.name}")
    await channel.send(embed=embed, view=PersonalRoomView())
    await ctx.send(f"✅ החדר נוצר: {channel.mention}")


async def ask_gemini(channel_id: int, user_message: str) -> str:
    history = chat_sessions.setdefault(channel_id, [])
    history.append({"role": "user", "parts": [{"text": user_message}]})
    trimmed = history[-20:]
    try:
        response = await asyncio.to_thread(
            lambda: gemini_client.models.generate_content(model=GEMINI_MODEL, contents=trimmed)
        )
        reply_text = response.text
    except Exception as e:
        print(f"שגיאת Gemini: {e}")
        reply_text = "⚠️ הייתה שגיאה בפנייה ל-AI, נסה שוב בעוד רגע."
    history.append({"role": "model", "parts": [{"text": reply_text}]})
    chat_sessions[channel_id] = trimmed
    return reply_text


# ============================================================
#  2) מערכת משוב אנונימי - !feedback_setup
# ============================================================
class FeedbackModal(Modal, title="שליחת משוב"):
    def __init__(self, staff_member: discord.Member):
        super().__init__()
        self.staff_member = staff_member
        self.feedback_text = TextInput(
            label=f"המשוב שלך על {staff_member.display_name}",
            style=discord.TextStyle.paragraph,
            placeholder="כתוב כאן את המשוב שלך...",
            max_length=1000, required=True,
        )
        self.add_item(self.feedback_text)

    async def on_submit(self, interaction: discord.Interaction):
        feedback = self.feedback_text.value
        report_embed = discord.Embed(
            title="📩 משוב אנונימי חדש התקבל", description=feedback, color=discord.Color.gold(),
        )
        report_embed.add_field(name="👤 על חבר הצוות", value=self.staff_member.mention, inline=True)
        report_embed.set_footer(text="המשוב נשלח באופן אנונימי — זהות השולח אינה נשמרת")

        if FEEDBACK_CHANNEL_ID:
            channel = interaction.client.get_channel(FEEDBACK_CHANNEL_ID)
            if channel:
                await channel.send(embed=report_embed)

        try:
            dm_embed = discord.Embed(title="📩 קיבלת משוב אנונימי", description=feedback, color=discord.Color.blue())
            dm_embed.set_footer(text="משוב זה נשלח דרך מערכת המשוב האנונימית")
            await self.staff_member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.response.send_message("✅ המשוב שלך נשלח בהצלחה ובאופן אנונימי!", ephemeral=True)


class StaffSelect(Select):
    def __init__(self, staff_members: list[discord.Member]):
        options = [discord.SelectOption(label=m.display_name, value=str(m.id)) for m in staff_members[:25]]
        super().__init__(placeholder="בחר על מי לשלוח משוב...", options=options, custom_id="staff_select")

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0]))
        await interaction.response.send_modal(FeedbackModal(member))


class StaffSelectView(View):
    def __init__(self, staff_members: list[discord.Member]):
        super().__init__(timeout=60)
        self.add_item(StaffSelect(staff_members))


class FeedbackEntryView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="שלח משוב", style=discord.ButtonStyle.primary, emoji="📝", custom_id="send_feedback_button")
    async def send_feedback(self, interaction: discord.Interaction, button: Button):
        role = discord.utils.get(interaction.guild.roles, name=STAFF_ROLE_NAME)
        if role is None or len(role.members) == 0:
            await interaction.response.send_message(f"⚠️ לא נמצאו חברי צוות עם התפקיד '{STAFF_ROLE_NAME}'.", ephemeral=True)
            return
        await interaction.response.send_message("בחר על מי אתה רוצה לשלוח משוב:", view=StaffSelectView(role.members), ephemeral=True)


@bot.command(name="feedback_setup")
@commands.has_permissions(administrator=True)
async def feedback_setup(ctx):
    if not FEEDBACK_CHANNEL_ID:
        await ctx.send("⚠️ FEEDBACK_CHANNEL_ID לא מוגדר ב-.env - פקודה זו לא זמינה.")
        return
    embed = discord.Embed(
        title="📝 מערכת משוב חיובי/שלילי לצוות",
        description="בחרו איש צוות דרך הכפתור למטה, וכתבו לו משוב מפורט.\n\nכל משוב שאתם שולחים נשלח **בפרטיות ובאופן אנונימי** לחבר הצוות ולהנהלה בלבד!",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Powered By {BRAND_NAME}")
    await ctx.send(embed=embed, view=FeedbackEntryView())


# ============================================================
#  3) מערכת טיקטים מתקדמת - !ticket_setup, !leaderboard
# ============================================================
def load_stats() -> dict:
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_stats(stats: dict) -> None:
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def record_claim(user_id: int) -> None:
    stats = load_stats()
    key = str(user_id)
    stats[key] = stats.get(key, 0) + 1
    save_stats(stats)


CATEGORIES = {
    "general_questions": {"label": "שאלה כללית", "emoji": "❓", "short_name": "general", "color": 0x5865F2, "priority": "🟢 נמוכה", "eta": "עד 24 שעות"},
    "staff_application": {"label": "בחינה לצוות", "emoji": "📋", "short_name": "staff-app", "color": 0xFEE75C, "priority": "🟡 בינונית", "eta": "עד 3 ימי עסקים"},
    "ban_appeal": {"label": "ערעור על באן", "emoji": "🚫", "short_name": "appeal", "color": 0xED4245, "priority": "🔴 גבוהה", "eta": "עד 48 שעות"},
    "partner": {"label": "פרטנר", "emoji": "🤝", "short_name": "partner", "color": 0x57F287, "priority": "🟡 בינונית", "eta": "עד 5 ימי עסקים"},
    "community_servers": {"label": "שרתי קהילה", "emoji": "🌐", "short_name": "community", "color": 0xEB459E, "priority": "🟢 נמוכה", "eta": "עד 48 שעות"},
}


async def build_transcript_text(channel: discord.TextChannel) -> str:
    lines = [f"תמלול טיקט: {channel.name}", "=" * 50, ""]
    async for msg in channel.history(limit=200, oldest_first=True):
        time_str = msg.created_at.strftime("%Y-%m-%d %H:%M")
        content = msg.content or "[הודעה ללא טקסט / מכילה embed]"
        lines.append(f"[{time_str}] {msg.author.display_name}: {content}")
    return "\n".join(lines)


def make_transcript_file(text: str, channel_name: str) -> discord.File:
    return discord.File(fp=io.BytesIO(text.encode("utf-8")), filename=f"{channel_name}-transcript.txt")


class TicketDetailsModal(Modal, title="פרטי הטיקט"):
    def __init__(self, category_key: str):
        super().__init__()
        self.category_key = category_key
        self.subject = TextInput(label="נושא קצר", placeholder="לדוגמה: לא הצלחתי להתחבר לשרת", max_length=100, required=True)
        self.details = TextInput(label="תיאור מפורט", style=discord.TextStyle.paragraph, placeholder="פרט כמה שיותר מידע רלוונטי...", max_length=1000, required=True)
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        info = CATEGORIES[self.category_key]
        channel = interaction.channel
        owner = interaction.user

        new_name = f"{info['emoji']}┃{info['short_name']}-{owner.name}"
        await channel.edit(name=new_name[:100], topic=f"ticket-owner:{owner.id}|subject:{self.category_key}")

        active_tickets[channel.id] = {"subject": self.subject.value, "priority": info["priority"], "claimed_by": None}

        embed = branded_embed(title=f"{info['emoji']}  {info['label']}", color=info["color"])
        embed.description = f"**{self.subject.value}**\n\n{self.details.value}\n\n{DIVIDER}"
        embed.add_field(name="👤 נפתח על ידי", value=owner.mention, inline=True)
        embed.add_field(name="🙋 בטיפול על ידי", value="טרם נתפס", inline=True)
        embed.add_field(name="📊 סטטוס", value="🟢 פתוח", inline=True)
        embed.add_field(name="⚡ עדיפות", value=info["priority"], inline=True)
        embed.add_field(name="⏱️ זמן מענה משוער", value=info["eta"], inline=True)

        await interaction.response.edit_message(embed=embed, view=TicketManageView())


class CategorySelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=i["label"], value=k, emoji=i["emoji"]) for k, i in CATEGORIES.items()]
        super().__init__(placeholder="בחרו קטגוריה לפתיחת הטיקט", options=options, custom_id="ticket_category_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketDetailsModal(self.values[0]))


class CategorySelectView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CategorySelect())


class RatingView(View):
    def __init__(self, ticket_subject: str):
        super().__init__(timeout=300)
        self.ticket_subject = ticket_subject

    async def _rate(self, interaction: discord.Interaction, stars: int):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"תודה על הדירוג! ⭐ נתת {stars}/5 כוכבים.", view=self)
        if LOG_CHANNEL_ID:
            log_channel = interaction.client.get_channel(int(LOG_CHANNEL_ID))
            if log_channel:
                embed = branded_embed(
                    title="⭐ דירוג שירות חדש",
                    description=f"**נושא:** {self.ticket_subject}\n**דירוג:** {'⭐' * stars}{'☆' * (5 - stars)}\n**מאת:** {interaction.user.mention}",
                    color=0xFEE75C,
                )
                await log_channel.send(embed=embed)

    @discord.ui.button(emoji="⭐", style=discord.ButtonStyle.secondary)
    async def r1(self, interaction, button): await self._rate(interaction, 1)
    @discord.ui.button(emoji="⭐⭐", style=discord.ButtonStyle.secondary)
    async def r2(self, interaction, button): await self._rate(interaction, 2)
    @discord.ui.button(emoji="⭐⭐⭐", style=discord.ButtonStyle.secondary)
    async def r3(self, interaction, button): await self._rate(interaction, 3)
    @discord.ui.button(emoji="⭐⭐⭐⭐", style=discord.ButtonStyle.secondary)
    async def r4(self, interaction, button): await self._rate(interaction, 4)
    @discord.ui.button(emoji="⭐⭐⭐⭐⭐", style=discord.ButtonStyle.success)
    async def r5(self, interaction, button): await self._rate(interaction, 5)


class CloseReasonModal(Modal, title="סגירת טיקט"):
    reason = TextInput(label="סיבת הסגירה", style=discord.TextStyle.paragraph, placeholder="לדוגמה: הבעיה נפתרה בהצלחה", max_length=300, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.channel
        topic = channel.topic or ""
        owner_id = None
        if "ticket-owner:" in topic:
            try:
                owner_id = int(topic.split("ticket-owner:")[1].split("|")[0])
            except (ValueError, IndexError):
                pass

        ticket_data = active_tickets.get(channel.id, {})
        subject = ticket_data.get("subject", channel.name)

        await interaction.response.send_message("🔒 יוצר תמלול וסוגר את הטיקט...", ephemeral=True)

        try:
            transcript_text = await build_transcript_text(channel)
            log_embed = branded_embed(
                title="🔒 טיקט נסגר",
                description=f"**נושא:** {subject}\n**ערוץ:** {channel.name}\n**נסגר על ידי:** {interaction.user.mention}\n**סיבה:** {self.reason.value}",
                color=0xED4245,
            )
            if owner_id:
                log_embed.add_field(name="פותח הטיקט", value=f"<@{owner_id}>", inline=True)

            if LOG_CHANNEL_ID:
                try:
                    log_channel = interaction.guild.get_channel(int(LOG_CHANNEL_ID))
                    if log_channel:
                        await log_channel.send(embed=log_embed, file=make_transcript_file(transcript_text, channel.name))
                except (ValueError, discord.HTTPException) as e:
                    print(f"⚠️ שגיאה בשליחה לערוץ הלוג: {e}")

            if owner_id:
                owner = interaction.guild.get_member(owner_id)
                if owner:
                    try:
                        dm_embed = branded_embed(
                            title="🔒 הטיקט שלך נסגר",
                            description=f"**נושא:** {subject}\n**סיבת סגירה:** {self.reason.value}\n\nמצורף תמלול השיחה המלא.\n\nאיך הייתה ההתנהלות של הצוות בטיקט הזה? דרג/י מ-1 עד 5 ⭐",
                            color=0xED4245,
                        )
                        await owner.send(embed=dm_embed, file=make_transcript_file(transcript_text, channel.name), view=RatingView(subject))
                    except discord.Forbidden:
                        print(f"⚠️ לא ניתן לשלוח DM ל-{owner} - כנראה DM סגורים")
        except Exception as e:
            print(f"❌ שגיאה בתהליך סגירת הטיקט: {e}")
            try:
                await interaction.followup.send(f"⚠️ הייתה שגיאה בתמלול/דיווח, אבל הטיקט עדיין ייסגר: {e}", ephemeral=True)
            except discord.HTTPException:
                pass

        active_tickets.pop(channel.id, None)
        await asyncio.sleep(4)
        await channel.delete()


class TicketManageView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="מטופל על ידי", style=discord.ButtonStyle.success, emoji="🙋", custom_id="ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                f"⚠️ רק חברי צוות עם התפקיד **{STAFF_ROLE_NAME}** יכולים לקחת טיקטים.\n"
                f"(יש לך: {', '.join(r.name for r in interaction.user.roles if r.name != '@everyone') or 'ללא תפקידים'})",
                ephemeral=True,
            )
            return
        try:
            message = interaction.message
            embed = message.embeds[0]
            embed.set_field_at(1, name="🙋 בטיפול על ידי", value=interaction.user.mention, inline=True)
            embed.set_field_at(2, name="📊 סטטוס", value="🟡 בטיפול", inline=True)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            button.label = f"בטיפול: {interaction.user.display_name}"
            button.disabled = True
            if interaction.channel.id in active_tickets:
                active_tickets[interaction.channel.id]["claimed_by"] = interaction.user.id
            record_claim(interaction.user.id)
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"❌ שגיאה בתפיסת טיקט: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"⚠️ שגיאה: {e}", ephemeral=True)

    @discord.ui.button(label="אפשרויות צוות", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="ticket_staff_options")
    async def staff_options(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message(f"⚠️ רק חברי צוות עם התפקיד **{STAFF_ROLE_NAME}** יכולים לגשת לאפשרויות אלו.", ephemeral=True)
            return
        await interaction.response.send_message("בחר פעולה:", view=StaffOptionsView(), ephemeral=True)


class PrioritySelect(Select):
    def __init__(self, message: discord.Message):
        self.message = message
        options = [
            discord.SelectOption(label="נמוכה", value="🟢 נמוכה", emoji="🟢"),
            discord.SelectOption(label="בינונית", value="🟡 בינונית", emoji="🟡"),
            discord.SelectOption(label="גבוהה", value="🔴 גבוהה", emoji="🔴"),
        ]
        super().__init__(placeholder="בחר עדיפות חדשה", options=options)

    async def callback(self, interaction: discord.Interaction):
        embed = self.message.embeds[0]
        for idx, field in enumerate(embed.fields):
            if "עדיפות" in field.name:
                embed.set_field_at(idx, name="⚡ עדיפות", value=self.values[0], inline=True)
                break
        await self.message.edit(embed=embed)
        if self.message.channel.id in active_tickets:
            active_tickets[self.message.channel.id]["priority"] = self.values[0]
        await interaction.response.edit_message(content=f"✅ העדיפות עודכנה ל-{self.values[0]}", view=None)


class PriorityChangeView(View):
    def __init__(self, message: discord.Message):
        super().__init__(timeout=60)
        self.add_item(PrioritySelect(message))


class RemoveUserModal(Modal, title="הסרת משתמש מהטיקט"):
    user_id_input = TextInput(label="מזהה משתמש (User ID) או @תיוג", placeholder="לדוגמה: 123456789012345678", max_length=100, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.user_id_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        try:
            member = interaction.guild.get_member(int(raw))
        except ValueError:
            member = None
        if not member:
            await interaction.response.send_message("⚠️ לא נמצא משתמש כזה בשרת.", ephemeral=True)
            return
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"✅ {member.mention} הוסר מהטיקט.", ephemeral=True)


class StaffOptionsView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="סגור טיקט", style=discord.ButtonStyle.danger, emoji="🔒", row=0)
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CloseReasonModal())

    @discord.ui.button(label="הוסף משתמש", style=discord.ButtonStyle.primary, emoji="➕", row=0)
    async def add_user_info(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("כדי להוסיף משתמש, כתבו בערוץ: `!add @משתמש`", ephemeral=True)

    @discord.ui.button(label="הסר משתמש", style=discord.ButtonStyle.primary, emoji="➖", row=0)
    async def remove_user(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(RemoveUserModal())

    @discord.ui.button(label="נעל טיקט", style=discord.ButtonStyle.secondary, emoji="🔇", row=1)
    async def lock_ticket(self, interaction: discord.Interaction, button: Button):
        topic = interaction.channel.topic or ""
        owner_id = None
        if "ticket-owner:" in topic:
            try:
                owner_id = int(topic.split("ticket-owner:")[1].split("|")[0])
            except (ValueError, IndexError):
                pass
        if owner_id:
            owner = interaction.guild.get_member(owner_id)
            if owner:
                await interaction.channel.set_permissions(owner, send_messages=False)
        await interaction.response.send_message("🔇 הטיקט ננעל - רק צוות יכול לכתוב כעת.", ephemeral=False)

    @discord.ui.button(label="שחרר נעילה", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def unlock_ticket(self, interaction: discord.Interaction, button: Button):
        topic = interaction.channel.topic or ""
        owner_id = None
        if "ticket-owner:" in topic:
            try:
                owner_id = int(topic.split("ticket-owner:")[1].split("|")[0])
            except (ValueError, IndexError):
                pass
        if owner_id:
            owner = interaction.guild.get_member(owner_id)
            if owner:
                await interaction.channel.set_permissions(owner, send_messages=True)
        await interaction.response.send_message("🔊 הנעילה הוסרה - המשתמש יכול לכתוב שוב.", ephemeral=False)

    @discord.ui.button(label="שנה עדיפות", style=discord.ButtonStyle.secondary, emoji="🚩", row=1)
    async def change_priority(self, interaction: discord.Interaction, button: Button):
        messages = [m async for m in interaction.channel.history(limit=20) if m.author == interaction.client.user and m.embeds]
        if not messages:
            await interaction.response.send_message("⚠️ לא נמצאה הודעת טיקט לעדכון.", ephemeral=True)
            return
        await interaction.response.send_message("בחר את רמת העדיפות החדשה:", view=PriorityChangeView(messages[0]), ephemeral=True)

    @discord.ui.button(label="תזכורת למשתמש", style=discord.ButtonStyle.secondary, emoji="🔔", row=1)
    async def remind_user(self, interaction: discord.Interaction, button: Button):
        topic = interaction.channel.topic or ""
        owner_id = None
        if "ticket-owner:" in topic:
            try:
                owner_id = int(topic.split("ticket-owner:")[1].split("|")[0])
            except (ValueError, IndexError):
                pass
        mention = f"<@{owner_id}>" if owner_id else "המשתמש"
        await interaction.response.send_message(f"🔔 {mention}, יש עדכון בטיקט - נשמח לתגובתך!", ephemeral=False)


@bot.command(name="add")
@commands.has_permissions(manage_channels=True)
async def add_user(ctx, member: discord.Member):
    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True, read_message_history=True)
    await ctx.send(f"✅ {member.mention} נוסף לטיקט.")


class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="פתיחת טיקט", style=discord.ButtonStyle.primary, emoji="📩", custom_id="open_ticket_button")
    async def open_ticket(self, interaction: discord.Interaction, button: Button):
        guild = interaction.guild
        member = interaction.user
        existing = discord.utils.find(lambda c: member.name in c.name, guild.text_channels)
        if existing and existing.topic and f"ticket-owner:{member.id}" in existing.topic:
            await interaction.response.send_message(f"⚠️ כבר יש לך טיקט פתוח: {existing.mention}", ephemeral=True)
            return

        staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)

        category = guild.get_channel(int(TICKET_CATEGORY_ID)) if TICKET_CATEGORY_ID else None
        ticket_number = next(ticket_counter)
        channel = await guild.create_text_channel(
            name=f"ticket-{member.name}", overwrites=overwrites, category=category,
            topic=f"ticket-owner:{member.id}|subject:pending",
        )
        embed = branded_embed(
            title=f"🎫 טיקט #{ticket_number:04d}",
            description=f"{member.mention}, בחרו קטגוריה מהתפריט למטה כדי שנוכל לסייע לכם בצורה הכי מדויקת ומהירה.\n\n{DIVIDER}",
        )
        await channel.send(content=member.mention, embed=embed, view=CategorySelectView())
        await interaction.response.send_message(f"✅ הטיקט שלך נפתח: {channel.mention}", ephemeral=True)


@bot.command(name="ticket_setup")
@commands.has_permissions(administrator=True)
async def ticket_setup(ctx):
    embed = branded_embed(
        title="🎫 מערכת טיקטים",
        description=(
            f"פתחו טיקט כדי לקבל עזרה אישית מצוות {BRAND_NAME}. אנא קראו את הכללים לפני הפתיחה:\n\n"
            f"{DIVIDER}\n"
            "**[1]** אין לפתוח טיקט לצורך בדיחה בלבד\n"
            "**[2]** אין לתייג את הצוות בתוך הטיקט\n"
            "**[3]** אין להטריד או ליצור עומס מיותר בטיקטים\n"
            "**[4]** כל חוקי השרת חלים גם בתוך הטיקטים\n"
            f"{DIVIDER}"
        ),
    )
    embed.set_thumbnail(url=BRAND_LOGO_URL)
    await ctx.send(embed=embed, view=TicketPanelView())


@bot.command(name="ticket_stats")
@commands.has_permissions(administrator=True)
async def ticket_stats(ctx):
    embed = branded_embed(title="📊 סטטיסטיקת טיקטים", description=f"**טיקטים פתוחים כרגע:** {len(active_tickets)}")
    await ctx.send(embed=embed)


@bot.command(name="leaderboard", aliases=["top", "topickets", "טופ"])
async def leaderboard(ctx):
    stats = load_stats()
    if not stats:
        embed = branded_embed(title="🏆 טופ טיקטים", description="עדיין לא נאספו נתונים - חכו עד שחברי הצוות יתחילו לטפל בטיקטים!")
        await ctx.send(embed=embed)
        return

    sorted_stats = sorted(stats.items(), key=lambda item: item[1], reverse=True)[:10]
    max_count = sorted_stats[0][1] if sorted_stats else 1
    medal_map = {0: "🥇", 1: "🥈", 2: "🥉"}

    lines = []
    for idx, (user_id, count) in enumerate(sorted_stats):
        member = ctx.guild.get_member(int(user_id))
        name = member.mention if member else f"<@{user_id}>"
        rank_icon = medal_map.get(idx, f"**#{idx + 1}**")
        filled = round((count / max_count) * 12) if max_count else 0
        bar = "▰" * filled + "▱" * (12 - filled)
        lines.append(f"{rank_icon}  {name}\n`{bar}` **{count}** טיקטים")

    total_handled = sum(stats.values())
    top_member = ctx.guild.get_member(int(sorted_stats[0][0])) if sorted_stats else None

    embed = discord.Embed(
        title="🏆  לוח המובילים | SkyzoneIL",
        description=f"דירוג חברי הצוות לפי כמות הטיקטים שטופלו בהצלחה\n{DIVIDER}\n\n" + "\n\n".join(lines),
        color=0xFFD700,
    )
    embed.set_author(name=f"{BRAND_NAME} | Staff Rankings", icon_url=BRAND_LOGO_URL)
    if top_member:
        embed.set_thumbnail(url=top_member.display_avatar.url)
    embed.add_field(name="📈 סה״כ טיפולים", value=f"**{total_handled}**", inline=True)
    embed.add_field(name="👥 צוות פעיל", value=f"**{len(stats)}**", inline=True)
    embed.add_field(name="👑 המוביל", value=top_member.mention if top_member else "—", inline=True)
    embed.set_footer(text=f"{BRAND_NAME} © Support System", icon_url=BRAND_LOGO_URL)
    embed.timestamp = discord.utils.utcnow()
    await ctx.send(embed=embed)


# ============================================================
#  אירועי הבוט המשותפים
# ============================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    if gemini_client and message.channel.name.startswith("💬-chat-") and not message.content.startswith("!"):
        async with message.channel.typing():
            reply = await ask_gemini(message.channel.id, message.content)
        await message.channel.send(f"🤖 **Gemini:** {reply}")
    await bot.process_commands(message)


@bot.event
async def on_ready():
    bot.add_view(PersonalRoomView())
    bot.add_view(FeedbackEntryView())
    bot.add_view(TicketPanelView())
    bot.add_view(CategorySelectView())
    bot.add_view(TicketManageView())
    print(f"✅ הבוט המאוחד מחובר בתור: {bot.user}")
    print(f"   - חדר AI אישי (!pchat): {'✅ פעיל' if gemini_client else '❌ לא פעיל (חסר GEMINI_API_KEY)'}")
    print(f"   - מערכת משוב (!feedback_setup): {'✅ פעיל' if FEEDBACK_CHANNEL_ID else '❌ לא פעיל (חסר FEEDBACK_CHANNEL_ID)'}")
    print(f"   - מערכת טיקטים (!ticket_setup): ✅ פעיל")


bot.run(DISCORD_TOKEN)
