import os
print("bot.py started")

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

print("Token loaded?", bool(os.getenv("DISCORD_TOKEN")))

import json
import secrets
from typing import Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

CONFIG_FILE = "roll_config.json"


# -----------------------------
# Config helpers (per guild)
# -----------------------------
def load_config() -> Dict[str, dict]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: Dict[str, dict]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def guild_key(guild_id: int) -> str:
    return str(guild_id)


def default_guild_config() -> dict:
    return {
        # Emoji users react with to roll
        "trigger_emoji": "🎲",

        # The message users must react to (set by /postroller)
        "trigger_message_id": None,
        "trigger_channel_id": None,

        # Where to log rolls for moderators
        "mod_channel_id": None,

        # Weighted outcomes (must total 100)
        "outcomes": [
            {"name": "powers, curse", "weight": 25},
            {"name": "powers, blessing", "weight": 25},
            {"name": "no powers", "weight": 50},
        ],
    }


def get_guild_config(cfg: Dict[str, dict], guild_id: int) -> dict:
    k = guild_key(guild_id)
    if k not in cfg:
        cfg[k] = default_guild_config()
        save_config(cfg)
    return cfg[k]


def validate_outcomes(outcomes: List[dict]) -> Tuple[bool, str]:
    if not outcomes:
        return False, "You must provide at least one outcome."
    total = 0
    for o in outcomes:
        if "name" not in o or "weight" not in o:
            return False, "Each outcome must have 'name' and 'weight'."
        if not isinstance(o["name"], str) or not o["name"].strip():
            return False, "Outcome name must be a non-empty string."
        if not isinstance(o["weight"], int) or o["weight"] < 0:
            return False, "Outcome weight must be a non-negative integer."
        total += o["weight"]
    if total != 100:
        return False, f"Outcome weights must total 100 (currently {total})."
    return True, "OK"


# -----------------------------
# True randomness helpers
# -----------------------------
def roll_d100() -> int:
    return secrets.randbelow(100) + 1


def weighted_choice(outcomes: List[dict]) -> str:
    r = secrets.randbelow(100)  # 0..99
    cumulative = 0
    for o in outcomes:
        cumulative += o["weight"]
        if r < cumulative:
            return o["name"]
    return outcomes[-1]["name"]


# -----------------------------
# Bot setup
# -----------------------------
intents = discord.Intents.default()
intents.reactions = True
intents.guilds = True
intents.members = False
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
cfg = load_config()


def is_mod(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not interaction.user:
        return False
    perms = interaction.user.guild_permissions
    return perms.manage_messages or perms.administrator


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print("Command sync failed:", e)

    print(f"Logged in as {bot.user} (id={bot.user.id})")


# -----------------------------
# Slash commands
# -----------------------------
@bot.tree.command(name="setmodchannel", description="Set the moderator log channel for rolls.")
@app_commands.describe(channel="The channel where roll logs should be sent")
async def setmodchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not is_mod(interaction):
        return await interaction.response.send_message("You need Manage Messages (or Admin) to do this.", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    gconf["mod_channel_id"] = channel.id
    save_config(cfg)

    await interaction.response.send_message(f"✅ Mod log channel set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="setemoji", description="Set the emoji used to trigger the roll reaction.")
@app_commands.describe(emoji="Emoji users react with (e.g. 🎲)")
async def setemoji(interaction: discord.Interaction, emoji: str):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not is_mod(interaction):
        return await interaction.response.send_message("You need Manage Messages (or Admin) to do this.", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    gconf["trigger_emoji"] = emoji
    save_config(cfg)

    await interaction.response.send_message(f"✅ Trigger emoji set to: {emoji}", ephemeral=True)


@bot.tree.command(name="setodds", description="Set the weighted outcome percentages (must total 100).")
@app_commands.describe(
    odds="Format: name=weight; name=weight; ... (example: powers, curse=25; powers, blessing=25; no powers=50)"
)
async def setodds(interaction: discord.Interaction, odds: str):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not is_mod(interaction):
        return await interaction.response.send_message("You need Manage Messages (or Admin) to do this.", ephemeral=True)

    parsed: List[dict] = []
    parts = [p.strip() for p in odds.split(";") if p.strip()]
    for p in parts:
        if "=" not in p:
            return await interaction.response.send_message("❌ Bad format. Use: name=weight; ...", ephemeral=True)
        name, weight_str = p.split("=", 1)
        name = name.strip()
        weight_str = weight_str.strip()
        if not weight_str.isdigit():
            return await interaction.response.send_message(f"❌ Weight must be an integer: `{p}`", ephemeral=True)
        parsed.append({"name": name, "weight": int(weight_str)})

    ok, msg = validate_outcomes(parsed)
    if not ok:
        return await interaction.response.send_message(f"❌ {msg}", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    gconf["outcomes"] = parsed
    save_config(cfg)

    pretty = "\n".join([f"- **{o['name']}**: {o['weight']}%" for o in parsed])
    await interaction.response.send_message(f"✅ Odds updated:\n{pretty}", ephemeral=True)


@bot.tree.command(name="postroller", description="Post the dice roller message users react to, and auto-add the emoji.")
@app_commands.describe(channel="Where to post the roller message", message="Text to show above the roller")
async def postroller(interaction: discord.Interaction, channel: discord.TextChannel, message: str = "React to roll!"):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not is_mod(interaction):
        return await interaction.response.send_message("You need Manage Messages (or Admin) to do this.", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    emoji = gconf["trigger_emoji"]

    roller_msg = await channel.send(f"{message}\n\nReact with {emoji} to roll a **d100**.")
    try:
        await roller_msg.add_reaction(emoji)
    except discord.HTTPException:
        pass

    gconf["trigger_message_id"] = roller_msg.id
    gconf["trigger_channel_id"] = channel.id
    save_config(cfg)

    await interaction.response.send_message(
        f"✅ Roller posted in {channel.mention} (message id: `{roller_msg.id}`)\nUsers must react with {emoji}.",
        ephemeral=True,
    )


@bot.tree.command(name="showodds", description="Show current odds configuration.")
async def showodds(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    outs = gconf["outcomes"]
    pretty = "\n".join([f"- **{o['name']}**: {o['weight']}%" for o in outs])
    mod_ch = gconf["mod_channel_id"]
    trig_mid = gconf["trigger_message_id"]
    trig_emoji = gconf["trigger_emoji"]

    await interaction.response.send_message(
        f"**Trigger emoji:** {trig_emoji}\n"
        f"**Trigger message id:** {trig_mid}\n"
        f"**Mod channel:** {f'<#{mod_ch}>' if mod_ch else 'Not set'}\n\n"
        f"**Odds:**\n{pretty}",
        ephemeral=True,
    )


@bot.tree.command(name="editrolllog", description="(Mods) Edit a roll log message the bot posted in the mod channel.")
@app_commands.describe(message_id="The message ID of the log message", new_text="What the message should say now")
async def editrolllog(interaction: discord.Interaction, message_id: str, new_text: str):
    if not interaction.guild:
        return await interaction.response.send_message("Use this in a server.", ephemeral=True)
    if not is_mod(interaction):
        return await interaction.response.send_message("You need Manage Messages (or Admin) to do this.", ephemeral=True)

    gconf = get_guild_config(cfg, interaction.guild.id)
    mod_channel_id = gconf["mod_channel_id"]
    if not mod_channel_id:
        return await interaction.response.send_message("❌ Mod channel is not set. Use /setmodchannel.", ephemeral=True)

    ch = interaction.guild.get_channel(mod_channel_id)
    if not isinstance(ch, discord.TextChannel):
        return await interaction.response.send_message("❌ Mod channel not found.", ephemeral=True)

    try:
        mid = int(message_id)
    except ValueError:
        return await interaction.response.send_message("❌ message_id must be a number.", ephemeral=True)

    try:
        msg = await ch.fetch_message(mid)
    except discord.NotFound:
        return await interaction.response.send_message("❌ That message ID wasn't found in the mod channel.", ephemeral=True)

    if msg.author.id != bot.user.id:
        return await interaction.response.send_message("❌ I can only edit my own log messages.", ephemeral=True)

    await msg.edit(content=new_text)
    await interaction.response.send_message("✅ Edited the roll log message.", ephemeral=True)


# -----------------------------
# Reaction trigger (emoji roll)
# -----------------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return

    gconf = get_guild_config(cfg, guild.id)
    trigger_message_id = gconf.get("trigger_message_id")
    trigger_channel_id = gconf.get("trigger_channel_id")
    trigger_emoji = gconf.get("trigger_emoji")

    if not trigger_message_id or not trigger_channel_id:
        return

    if payload.message_id != trigger_message_id or payload.channel_id != trigger_channel_id:
        return

    if str(payload.emoji) != str(trigger_emoji):
        return

    member = guild.get_member(payload.user_id)
    user = member if member is not None else await bot.fetch_user(payload.user_id)

    d100 = roll_d100()
    outcome = weighted_choice(gconf["outcomes"])

    dm_ok = True
    try:
        await user.send(
            f"🎲 **Your roll:** {d100}/100\n"
            f"✨ **Outcome:** {outcome}\n\n"
            f"(Triggered by reacting with {trigger_emoji} in **{guild.name}**.)"
        )
    except (discord.Forbidden, discord.HTTPException):
        dm_ok = False

    mod_channel_id = gconf.get("mod_channel_id")
    if mod_channel_id:
        mod_ch = guild.get_channel(mod_channel_id)
        if isinstance(mod_ch, discord.TextChannel):
            content = (
                f"📋 **Roll Log**\n"
                f"User: {user.mention} (`{user.id}`)\n"
                f"Roll: **{d100}**/100\n"
                f"Outcome: **{outcome}**\n"
                f"DM delivered: {'✅' if dm_ok else '❌ (user has DMs closed?)'}\n"
                f"Trigger message: `{trigger_message_id}` in <#{trigger_channel_id}>"
            )
            await mod_ch.send(content)

    try:
        channel = guild.get_channel(payload.channel_id)
        if isinstance(channel, discord.TextChannel):
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, user)
    except Exception:
        pass


# -----------------------------
# Run the bot
# -----------------------------
TOKEN = os.getenv("DISCORD_TOKEN")

print("Token exists:", bool(TOKEN))
print("Has dot:", "." in TOKEN if TOKEN else None)

if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN environment variable.")

bot.run(TOKEN)

