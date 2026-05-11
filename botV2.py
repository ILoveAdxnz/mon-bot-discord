import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
import random
from datetime import datetime, timedelta, timezone
import re

# ─────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────
PREFIX = "+"
TOKEN = os.getenv("DISCORD_TOKEN", "")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ─────────────────────────────────────────
#  Persistance (fichiers JSON)
# ─────────────────────────────────────────
WARNS_FILE    = "warns.json"
CONFIG_FILE   = "config.json"
GIVEAWAY_FILE = "giveaways.json"
TEMPBAN_FILE  = "tempbans.json"

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

warns_db    = load_json(WARNS_FILE)
config_db   = load_json(CONFIG_FILE)
giveaway_db = load_json(GIVEAWAY_FILE)
tempban_db  = load_json(TEMPBAN_FILE)

def save_warns():     save_json(WARNS_FILE, warns_db)
def save_config():    save_json(CONFIG_FILE, config_db)
def save_giveaways(): save_json(GIVEAWAY_FILE, giveaway_db)
def save_tempbans():  save_json(TEMPBAN_FILE, tempban_db)

def get_guild_cfg(guild_id: int) -> dict:
    key = str(guild_id)
    if key not in config_db:
        config_db[key] = {}
    return config_db[key]

# ─────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────
def parse_duration(text: str):
    """Convertit '10m', '2h', '1d' en timedelta. Retourne None si invalide."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    m = re.fullmatch(r"(\d+)([smhd])", text.strip().lower())
    if not m:
        return None
    return timedelta(seconds=int(m.group(1)) * units[m.group(2)])

def format_duration(td: timedelta) -> str:
    """Formate un timedelta en texte lisible."""
    total = int(td.total_seconds())
    if total >= 86400:
        return f"{total // 86400}j {(total % 86400) // 3600}h"
    elif total >= 3600:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    elif total >= 60:
        return f"{total // 60}m {total % 60}s"
    return f"{total}s"

async def send_log(guild: discord.Guild, embed: discord.Embed):
    cfg = get_guild_cfg(guild.id)
    ch_id = cfg.get("log_channel")
    if ch_id:
        ch = guild.get_channel(int(ch_id))
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

def mod_embed(title, description, color=discord.Color.red()):
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

def success_embed(title, description):
    return mod_embed(title, description, discord.Color.green())

def info_embed(title, description):
    return mod_embed(title, description, discord.Color.blurple())

def warning_embed(title, description):
    return mod_embed(title, description, discord.Color.yellow())

def check_hierarchy(ctx, member: discord.Member) -> bool:
    return ctx.author.top_role > member.top_role and ctx.guild.me.top_role > member.top_role

# ─────────────────────────────────────────
#  Événements
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    print(f"   Préfixe : {PREFIX}")
    check_giveaways.start()
    resume_tempbans.start()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name=f"{PREFIX}help · modération"))

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Argument manquant. Tape `{PREFIX}help {ctx.command}` pour l'aide.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ Tu n'as pas la permission d'utiliser cette commande.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.reply("❌ Je n'ai pas les permissions nécessaires.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.reply("❌ Membre introuvable.")
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("❌ Argument invalide.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.reply(f"⏳ Attends encore `{error.retry_after:.1f}s`.")
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        raise error

@bot.event
async def on_member_join(member):
    cfg = get_guild_cfg(member.guild.id)
    auto_role_id = cfg.get("auto_role")
    if auto_role_id:
        role = member.guild.get_role(int(auto_role_id))
        if role:
            try:
                await member.add_roles(role, reason="Auto-rôle à l'arrivée")
            except discord.Forbidden:
                pass
    welcome_ch_id = cfg.get("welcome_channel")
    welcome_msg   = cfg.get("welcome_message", "Bienvenue {mention} sur **{server}** !")
    if welcome_ch_id:
        ch = member.guild.get_channel(int(welcome_ch_id))
        if ch:
            text = (welcome_msg
                    .replace("{mention}", member.mention)
                    .replace("{server}", member.guild.name)
                    .replace("{name}", str(member)))
            await ch.send(text)

@bot.event
async def on_member_remove(member):
    """Log quand un membre quitte le serveur."""
    cfg = get_guild_cfg(member.guild.id)
    ch_id = cfg.get("log_channel")
    if not ch_id:
        return
    ch = member.guild.get_channel(int(ch_id))
    if not ch:
        return
    e = info_embed("👋 Membre parti", f"**Membre :** {member} (`{member.id}`)\n**Rejoint le :** <t:{int(member.joined_at.timestamp())}:R>")
    e.set_thumbnail(url=member.display_avatar.url)
    try:
        await ch.send(embed=e)
    except discord.Forbidden:
        pass

# ─────────────────────────────────────────
#  AIDE
# ─────────────────────────────────────────
@bot.command(name="help")
async def help_cmd(ctx, commande: str = None):
    """Affiche l'aide générale ou l'aide d'une commande."""
    if commande:
        cmd = bot.get_command(commande)
        if cmd:
            e = info_embed(f"{PREFIX}{cmd.name}", cmd.help or "Pas de description.")
            await ctx.send(embed=e)
            return
        await ctx.reply(f"❌ Commande `{commande}` inconnue.")
        return

    e = discord.Embed(
        title="🤖 Bot Complet — Aide",
        description=f"Préfixe : `{PREFIX}`  •  `{PREFIX}help <commande>` pour les détails",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    sections = {
        "🔨 Sanctions": [
            ("ban",      "<membre> [raison]",           "Bannir définitivement"),
            ("unban",    "<user_id> [raison]",           "Débannir un utilisateur"),
            ("kick",     "<membre> [raison]",            "Expulser un membre"),
            ("mute",     "<membre> <durée> [raison]",    "Mute (ex : 10m, 2h, 1d)"),
            ("unmute",   "<membre> [raison]",            "Retirer le mute"),
            ("warn",     "<membre> <raison>",            "Avertir un membre"),
            ("unwarn",   "<membre> <id_warn>",           "Supprimer un avertissement"),
            ("clearwarns","<membre>",                    "Effacer tous les warns d'un membre"),
            ("warns",    "[membre]",                     "Voir les avertissements"),
            ("softban",  "<membre> [raison]",            "Ban + déban immédiat"),
            ("tempban",  "<membre> <durée> <raison>",    "Ban temporaire"),
        ],
        "🧹 Nettoyage": [
            ("clear",     "<nombre|all> [membre]",       "Supprimer N messages ou tous (+clear all)"),
            ("purge",     "<membre> <nombre>",           "Supprimer les messages d'un membre"),
        ],
        "🔒 Gestion des salons": [
            ("lock",        "[salon]",                   "Verrouiller un salon"),
            ("unlock",      "[salon]",                   "Déverrouiller un salon"),
            ("slowmode",    "<secondes> [salon]",        "Définir le slowmode"),
            ("nuke",        "[salon]",                   "Recréer un salon"),
            ("createtext",  "<nom> [catégorie]",         "Créer un salon textuel"),
            ("createvoice", "<nom> [catégorie]",         "Créer un salon vocal"),
            ("createcat",   "<nom>",                     "Créer une catégorie"),
            ("deletechan",  "<salon>",                   "Supprimer un salon"),
            ("renamechan",  "<salon> <nouveau_nom>",     "Renommer un salon"),
        ],
        "🎉 Giveaways": [
            ("gcreate", "<durée> <gagnants> <prix>",     "Lancer un giveaway"),
            ("gend",    "<message_id>",                  "Terminer un giveaway immédiatement"),
            ("greroll", "<message_id>",                  "Tirer un nouveau gagnant"),
            ("glist",   "",                              "Lister les giveaways actifs"),
        ],
        "📢 Utilitaires": [
            ("poll",       "<question> | <opt1> | ...",  "Créer un sondage"),
            ("remind",     "<durée> <message>",          "Se rappeler quelque chose"),
            ("embed",      "<titre> | <description>",    "Envoyer un embed personnalisé"),
            ("announce",   "<message>",                  "Faire une annonce en embed"),
            ("ping",       "",                           "Latence du bot"),
            ("uptime",     "",                           "Temps d'activité du bot"),
            ("calc",       "<expression>",               "Calculatrice simple"),
            ("coinflip",   "",                           "Pile ou face"),
            ("roll",       "[NdN]",                      "Lancer des dés (ex: 2d6)"),
            ("say",        "<message>",                  "Faire parler le bot"),
        ],
        "👤 Membres & Rôles": [
            ("autorole",   "<rôle>",                     "Définir l'auto-rôle à l'arrivée"),
            ("setwelcome", "<salon> <message>",          "Configurer le message de bienvenue"),
            ("addrole",    "<membre> <rôle>",            "Donner un rôle à un membre"),
            ("removerole", "<membre> <rôle>",            "Retirer un rôle d'un membre"),
            ("avatar",     "[membre]",                   "Afficher l'avatar d'un membre"),
            ("userinfo",   "[membre]",                   "Infos sur un membre"),
            ("serverinfo", "",                           "Infos sur le serveur"),
            ("roleinfo",   "<rôle>",                     "Infos sur un rôle"),
            ("whois",      "<membre>",                   "Alias de userinfo"),
        ],
        "⚙️ Config": [
            ("setlog",     "<salon>",                    "Définir le salon de logs"),
            ("setmuterole","<rôle>",                     "Définir le rôle muet"),
        ],
    }
    for section, cmds in sections.items():
        val = "\n".join(f"`{PREFIX}{n}` {args} — {desc}" for n, args, desc in cmds)
        e.add_field(name=section, value=val, inline=False)
    e.set_footer(text=f"Demandé par {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=e)

# ─────────────────────────────────────────
#  SANCTIONS
# ─────────────────────────────────────────
@bot.command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    """Bannir définitivement un membre du serveur."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas bannir ce membre (hiérarchie).")
    try:
        await member.send(embed=mod_embed("🔨 Tu as été banni", f"**Serveur :** {ctx.guild.name}\n**Raison :** {reason}"))
    except Exception:
        pass
    await member.ban(reason=f"{ctx.author} : {reason}", delete_message_days=1)
    e = mod_embed("🔨 Membre banni", f"**Cible :** {member.mention} (`{member.id}`)\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban(ctx, user_id: int, *, reason: str = "Aucune raison fournie"):
    """Débannir un utilisateur via son ID."""
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{ctx.author} : {reason}")
        e = success_embed("✅ Membre débanni", f"**Cible :** {user} (`{user_id}`)\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}")
        await ctx.send(embed=e)
        await send_log(ctx.guild, e)
    except discord.NotFound:
        await ctx.reply("❌ Cet utilisateur n'est pas banni.")

@bot.command()
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    """Expulser un membre du serveur."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas expulser ce membre (hiérarchie).")
    try:
        await member.send(embed=mod_embed("👢 Tu as été expulsé", f"**Serveur :** {ctx.guild.name}\n**Raison :** {reason}", discord.Color.orange()))
    except Exception:
        pass
    await member.kick(reason=f"{ctx.author} : {reason}")
    e = mod_embed("👢 Membre expulsé", f"**Cible :** {member.mention} (`{member.id}`)\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}", discord.Color.orange())
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, duration: str, *, reason: str = "Aucune raison fournie"):
    """Rendre muet un membre. Durée : 10m, 2h, 1d (max 28j)."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas mute ce membre (hiérarchie).")
    delta = parse_duration(duration)
    if not delta:
        return await ctx.reply("❌ Durée invalide. Exemples : `10m`, `2h`, `1d`.")
    if delta > timedelta(days=28):
        return await ctx.reply("❌ Durée maximum : 28 jours.")
    until = datetime.now(timezone.utc) + delta
    await member.timeout(until, reason=f"{ctx.author} : {reason}")
    e = mod_embed(
        "🔇 Membre muet",
        f"**Cible :** {member.mention} (`{member.id}`)\n**Durée :** {duration}\n**Fin :** <t:{int(until.timestamp())}:R>\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}",
        discord.Color.orange()
    )
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(moderate_members=True)
@commands.bot_has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    """Retirer le mute d'un membre."""
    await member.timeout(None, reason=f"{ctx.author} : {reason}")
    e = success_embed("🔊 Mute retiré", f"**Cible :** {member.mention}\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason: str):
    """Avertir un membre et enregistrer l'avertissement."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas avertir ce membre (hiérarchie).")
    gid, uid = str(ctx.guild.id), str(member.id)
    warns_db.setdefault(gid, {}).setdefault(uid, [])
    entry = {"reason": reason, "date": datetime.now(timezone.utc).isoformat(), "mod": str(ctx.author.id)}
    warns_db[gid][uid].append(entry)
    save_warns()
    count = len(warns_db[gid][uid])
    e = warning_embed("⚠️ Avertissement", f"**Cible :** {member.mention} (`{member.id}`)\n**Raison :** {reason}\n**Total warns :** {count}\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)
    try:
        await member.send(embed=warning_embed("⚠️ Tu as reçu un avertissement", f"**Serveur :** {ctx.guild.name}\n**Raison :** {reason}\n**Total :** {count} warn(s)"))
    except Exception:
        pass

@bot.command()
@commands.has_permissions(kick_members=True)
async def unwarn(ctx, member: discord.Member, warn_id: int):
    """Supprimer un avertissement par son numéro (commence à 1)."""
    gid, uid = str(ctx.guild.id), str(member.id)
    w_list = warns_db.get(gid, {}).get(uid, [])
    if not w_list or warn_id < 1 or warn_id > len(w_list):
        return await ctx.reply(f"❌ Warn #{warn_id} introuvable.")
    removed = w_list.pop(warn_id - 1)
    save_warns()
    e = success_embed("🗑️ Warn supprimé", f"**Cible :** {member.mention}\n**Warn supprimé :** {removed['reason']}\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(kick_members=True)
async def clearwarns(ctx, member: discord.Member):
    """Effacer tous les avertissements d'un membre. Usage : +clearwarns @membre"""
    gid, uid = str(ctx.guild.id), str(member.id)
    count = len(warns_db.get(gid, {}).get(uid, []))
    if count == 0:
        return await ctx.reply(f"✅ {member.mention} n'a aucun avertissement à effacer.")
    warns_db.setdefault(gid, {})[uid] = []
    save_warns()
    e = success_embed("🧹 Warns effacés", f"**{count}** avertissement(s) supprimé(s) pour {member.mention}\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
async def warns(ctx, member: discord.Member = None):
    """Afficher les avertissements d'un membre."""
    member = member or ctx.author
    gid, uid = str(ctx.guild.id), str(member.id)
    w_list = warns_db.get(gid, {}).get(uid, [])
    if not w_list:
        return await ctx.reply(f"✅ {member.mention} n'a aucun avertissement.")
    e = warning_embed(f"⚠️ Warns de {member}", "")
    for i, w in enumerate(w_list, 1):
        ts     = w.get("date", "?")[:10]
        mod_id = w.get("mod")
        mod_str= f"<@{mod_id}>" if mod_id else "?"
        e.add_field(name=f"#{i} — {ts}", value=f"**Raison :** {w['reason']}\n**Mod :** {mod_str}", inline=False)
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def softban(ctx, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    """Bannir puis débannir immédiatement (supprime les messages récents)."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas softban ce membre.")
    await member.ban(reason=f"[SOFTBAN] {ctx.author} : {reason}", delete_message_days=7)
    await ctx.guild.unban(member, reason="Softban — déban automatique")
    e = mod_embed("🪃 Softban", f"**Cible :** {member.mention}\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}", discord.Color.orange())
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def tempban(ctx, member: discord.Member, duration: str, *, reason: str = "Aucune raison fournie"):
    """Bannir temporairement un membre. Durée : 10m, 2h, 1d."""
    if not check_hierarchy(ctx, member):
        return await ctx.reply("❌ Tu ne peux pas tempban ce membre.")
    delta = parse_duration(duration)
    if not delta:
        return await ctx.reply("❌ Durée invalide. Exemples : `10m`, `2h`, `1d`.")
    until = datetime.now(timezone.utc) + delta
    until_ts = int(until.timestamp())
    try:
        await member.send(embed=mod_embed("⏳ Ban temporaire", f"**Serveur :** {ctx.guild.name}\n**Durée :** {duration}\n**Raison :** {reason}"))
    except Exception:
        pass
    await member.ban(reason=f"[TEMPBAN {duration}] {ctx.author} : {reason}", delete_message_days=1)

    # Persistance du tempban
    gid = str(ctx.guild.id)
    tempban_db.setdefault(gid, {})[str(member.id)] = {
        "end_ts": until_ts,
        "reason": reason,
        "mod_id": str(ctx.author.id),
    }
    save_tempbans()

    e = mod_embed(
        "⏳ Ban temporaire",
        f"**Cible :** {member.mention} (`{member.id}`)\n**Durée :** {duration}\n**Fin :** <t:{until_ts}:R>\n**Modérateur :** {ctx.author.mention}\n**Raison :** {reason}"
    )
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

    async def unban_later():
        await asyncio.sleep(delta.total_seconds())
        try:
            user = await bot.fetch_user(member.id)
            await ctx.guild.unban(user, reason="Tempban expiré")
            tempban_db.get(gid, {}).pop(str(member.id), None)
            save_tempbans()
            ue = success_embed("✅ Tempban expiré", f"**Cible :** {user} (`{user.id}`) a été débanni automatiquement.")
            await send_log(ctx.guild, ue)
        except Exception:
            pass

    asyncio.ensure_future(unban_later())

# Tâche de reprise des tempbans après redémarrage
@tasks.loop(count=1)
async def resume_tempbans():
    """Replanifie les tempbans persistés au redémarrage."""
    await bot.wait_until_ready()
    now = datetime.now(timezone.utc).timestamp()
    for gid, bans in list(tempban_db.items()):
        guild = bot.get_guild(int(gid))
        if not guild:
            continue
        for uid, bdata in list(bans.items()):
            end_ts = bdata["end_ts"]
            remaining = end_ts - now
            if remaining <= 0:
                # Déban immédiat
                try:
                    user = await bot.fetch_user(int(uid))
                    await guild.unban(user, reason="Tempban expiré (reprise)")
                except Exception:
                    pass
                bans.pop(uid)
            else:
                async def _unban(g=guild, u_id=uid, delay=remaining, g_id=gid):
                    await asyncio.sleep(delay)
                    try:
                        user = await bot.fetch_user(int(u_id))
                        await g.unban(user, reason="Tempban expiré")
                        tempban_db.get(g_id, {}).pop(u_id, None)
                        save_tempbans()
                        ue = success_embed("✅ Tempban expiré", f"**Cible :** {user} (`{u_id}`) débanni automatiquement.")
                        await send_log(g, ue)
                    except Exception:
                        pass
                asyncio.ensure_future(_unban())
        save_tempbans()

# ─────────────────────────────────────────
#  NETTOYAGE — FIX + clear all + clear @user N
# ─────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def clear(ctx, amount: str, member: discord.Member = None):
    """Supprimer des messages. Usage : +clear <nombre|all> [@membre]
    
    Exemples :
      +clear 50           → supprime les 50 derniers messages
      +clear 20 @Pseudo   → supprime les 20 derniers messages de @Pseudo
      +clear all          → supprime TOUS les messages du salon (max 14j)
      +clear all @Pseudo  → supprime TOUS les messages de @Pseudo (max 14j)
    """
    await ctx.message.delete()

    is_all = amount.lower() == "all"
    if not is_all:
        try:
            amount_int = int(amount)
        except ValueError:
            return await ctx.send("❌ Utilise un nombre ou `all`. Exemple : `+clear 50` ou `+clear all`.", delete_after=5)
        if amount_int < 1 or amount_int > 500:
            return await ctx.send("❌ Nombre entre 1 et 500.", delete_after=5)

    # Limite Discord : 14 jours pour bulk delete
    after_limit = datetime.now(timezone.utc) - timedelta(days=14)

    if is_all:
        if member:
            def check_member(m): return m.author == member
            deleted = await ctx.channel.purge(limit=None, check=check_member, after=after_limit)
        else:
            deleted = await ctx.channel.purge(limit=None, after=after_limit)
    else:
        if member:
            def check_member(m): return m.author == member
            # On scanne 10x plus de messages pour trouver N messages du membre
            deleted = await ctx.channel.purge(
                limit=min(amount_int * 10, 1000),
                check=check_member,
                after=after_limit
            )
            # On ne garde que le nombre demandé
            if len(deleted) > amount_int:
                # Les messages supprimés en trop ne peuvent pas être récupérés,
                # mais on informe du vrai nombre
                pass
            deleted = deleted[:amount_int]
        else:
            deleted = await ctx.channel.purge(limit=amount_int, after=after_limit)

    cible_str = f" de {member.mention}" if member else ""
    e = success_embed(
        "🧹 Nettoyage",
        f"**{len(deleted)}** message(s) supprimé(s){cible_str}.\n**Modérateur :** {ctx.author.mention}"
    )
    msg = await ctx.send(embed=e)
    await asyncio.sleep(5)
    await msg.delete()
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def purge(ctx, member: discord.Member, amount: int = 100):
    """Supprimer les messages d'un membre spécifique (max 500). Usage : +purge @membre [nombre]"""
    if amount < 1 or amount > 500:
        return await ctx.reply("❌ Nombre entre 1 et 500.")
    await ctx.message.delete()
    after_limit = datetime.now(timezone.utc) - timedelta(days=14)
    def check(m): return m.author == member
    deleted = await ctx.channel.purge(limit=min(amount * 10, 2000), check=check, after=after_limit)
    deleted = deleted[:amount]
    e = success_embed("🧹 Purge", f"**{len(deleted)}** message(s) de {member.mention} supprimé(s).\n**Modérateur :** {ctx.author.mention}")
    msg = await ctx.send(embed=e)
    await asyncio.sleep(5)
    await msg.delete()
    await send_log(ctx.guild, e)

# ─────────────────────────────────────────
#  GESTION DES CANAUX
# ─────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def lock(ctx, channel: discord.TextChannel = None):
    """Verrouiller un salon (empêche @everyone d'envoyer des messages)."""
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Lock par {ctx.author}")
    e = mod_embed("🔒 Salon verrouillé", f"{channel.mention} a été verrouillé.\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def unlock(ctx, channel: discord.TextChannel = None):
    """Déverrouiller un salon."""
    channel = channel or ctx.channel
    overwrite = channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlock par {ctx.author}")
    e = success_embed("🔓 Salon déverrouillé", f"{channel.mention} est de nouveau ouvert.\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int, channel: discord.TextChannel = None):
    """Définir le slowmode d'un salon (0 = désactiver, max 21600s)."""
    channel = channel or ctx.channel
    if seconds < 0 or seconds > 21600:
        return await ctx.reply("❌ Valeur entre 0 et 21600 secondes.")
    await channel.edit(slowmode_delay=seconds, reason=f"Slowmode par {ctx.author}")
    label = f"{seconds}s" if seconds > 0 else "désactivé"
    e = info_embed("🐢 Slowmode", f"{channel.mention} — slowmode **{label}**.\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def nuke(ctx, channel: discord.TextChannel = None):
    """Recréer un salon identique (purge totale). Confirmation requise."""
    channel = channel or ctx.channel
    confirm_msg = await ctx.send(
        f"⚠️ **ATTENTION** : Tu vas supprimer et recréer {channel.mention}.\n"
        f"Tape `CONFIRMER` dans les 15 secondes pour continuer."
    )
    def check(m): return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRMER"
    try:
        await bot.wait_for("message", check=check, timeout=15)
    except asyncio.TimeoutError:
        await confirm_msg.delete()
        return await ctx.reply("❌ Nuke annulé.")
    pos = channel.position
    new_ch = await channel.clone(reason=f"Nuke par {ctx.author}")
    await channel.delete(reason=f"Nuke par {ctx.author}")
    await new_ch.edit(position=pos)
    e = mod_embed("💥 Salon nuke", f"{new_ch.mention} a été recréé.\n**Modérateur :** {ctx.author.mention}")
    await new_ch.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def createtext(ctx, nom: str, *, categorie: str = None):
    """Créer un salon textuel. Usage : +createtext nom [catégorie]"""
    category = None
    if categorie:
        category = discord.utils.get(ctx.guild.categories, name=categorie)
        if not category:
            return await ctx.reply(f"❌ Catégorie `{categorie}` introuvable.")
    channel = await ctx.guild.create_text_channel(nom, category=category, reason=f"Créé par {ctx.author}")
    e = success_embed("✅ Salon textuel créé", f"**Nom :** {channel.mention}\n**Catégorie :** {category.name if category else 'Aucune'}\n**Créé par :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def createvoice(ctx, nom: str, *, categorie: str = None):
    """Créer un salon vocal. Usage : +createvoice nom [catégorie]"""
    category = None
    if categorie:
        category = discord.utils.get(ctx.guild.categories, name=categorie)
        if not category:
            return await ctx.reply(f"❌ Catégorie `{categorie}` introuvable.")
    channel = await ctx.guild.create_voice_channel(nom, category=category, reason=f"Créé par {ctx.author}")
    e = success_embed("✅ Salon vocal créé", f"**Nom :** {channel.name}\n**Catégorie :** {category.name if category else 'Aucune'}\n**Créé par :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def createcat(ctx, *, nom: str):
    """Créer une catégorie. Usage : +createcat Nom de la catégorie"""
    category = await ctx.guild.create_category(nom, reason=f"Créé par {ctx.author}")
    e = success_embed("✅ Catégorie créée", f"**Nom :** {category.name}\n**Créé par :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def deletechan(ctx, channel: discord.abc.GuildChannel):
    """Supprimer un salon (textuel ou vocal). Usage : +deletechan #salon"""
    nom = channel.name
    await channel.delete(reason=f"Supprimé par {ctx.author}")
    e = mod_embed("🗑️ Salon supprimé", f"**Nom :** `{nom}`\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_channels=True)
@commands.bot_has_permissions(manage_channels=True)
async def renamechan(ctx, channel: discord.abc.GuildChannel, *, nouveau_nom: str):
    """Renommer un salon. Usage : +renamechan #salon nouveau-nom"""
    ancien = channel.name
    await channel.edit(name=nouveau_nom, reason=f"Renommé par {ctx.author}")
    e = info_embed("✏️ Salon renommé", f"**Ancien :** `{ancien}`\n**Nouveau :** `{nouveau_nom}`\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

# ─────────────────────────────────────────
#  GIVEAWAYS
# ─────────────────────────────────────────
GIVEAWAY_EMOJI = "🎉"

def giveaway_embed(prize: str, winners: int, end_ts: int, host: discord.Member, ended=False, winner_mentions=None):
    color  = discord.Color.green() if not ended else discord.Color.greyple()
    status = "🎉 **GIVEAWAY**" if not ended else "🏁 **GIVEAWAY TERMINÉ**"
    desc   = f"**Prix :** {prize}\n**Gagnants :** {winners}\n**Organisé par :** {host.mention}\n"
    if not ended:
        desc += f"**Se termine :** <t:{end_ts}:R>\n\nRéagis avec {GIVEAWAY_EMOJI} pour participer !"
    else:
        if winner_mentions:
            desc += f"**Gagnant(s) :** {', '.join(winner_mentions)}"
        else:
            desc += "**Aucun participant valide.**"
    e = discord.Embed(title=status, description=desc, color=color, timestamp=datetime.now(timezone.utc))
    return e

async def end_giveaway(guild: discord.Guild, channel_id: int, message_id: int):
    gid = str(guild.id)
    mid = str(message_id)
    gdata = giveaway_db.get(gid, {}).get(mid)
    if not gdata or gdata.get("ended"):
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return
    reaction = discord.utils.get(message.reactions, emoji=GIVEAWAY_EMOJI)
    participants = []
    if reaction:
        async for user in reaction.users():
            if not user.bot:
                participants.append(user)
    nb_winners = min(gdata["winners"], len(participants))
    winners = random.sample(participants, nb_winners) if participants else []
    winner_mentions = [w.mention for w in winners]
    host = guild.get_member(gdata["host_id"]) or await bot.fetch_user(gdata["host_id"])
    end_ts = gdata["end_ts"]
    await message.edit(embed=giveaway_embed(gdata["prize"], gdata["winners"], end_ts, host, ended=True, winner_mentions=winner_mentions))
    if winners:
        await channel.send(f"🎉 Félicitations {', '.join(winner_mentions)} ! Vous avez gagné **{gdata['prize']}** !")
    else:
        await channel.send("😢 Personne n'a participé au giveaway.")
    giveaway_db[gid][mid]["ended"] = True
    giveaway_db[gid][mid]["winner_ids"] = [w.id for w in winners]
    save_giveaways()
    return winners

@tasks.loop(seconds=15)
async def check_giveaways():
    now = datetime.now(timezone.utc).timestamp()
    for gid, giveaways in list(giveaway_db.items()):
        guild = bot.get_guild(int(gid))
        if not guild:
            continue
        for mid, gdata in list(giveaways.items()):
            if gdata.get("ended"):
                continue
            if now >= gdata["end_ts"]:
                await end_giveaway(guild, gdata["channel_id"], int(mid))

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gcreate(ctx, duration: str, winners: int, *, prize: str):
    """Lancer un giveaway. Usage : +gcreate <durée> <nb_gagnants> <prix>
    
    Exemples :
      +gcreate 1h 1 Nitro Discord
      +gcreate 30m 3 Carte Steam 20€
    """
    delta = parse_duration(duration)
    if not delta:
        return await ctx.reply("❌ Durée invalide. Exemples : `30m`, `1h`, `2d`.")
    if winners < 1:
        return await ctx.reply("❌ Il faut au moins 1 gagnant.")
    end_dt = datetime.now(timezone.utc) + delta
    end_ts = int(end_dt.timestamp())
    e = giveaway_embed(prize, winners, end_ts, ctx.author)
    msg = await ctx.send(embed=e)
    await msg.add_reaction(GIVEAWAY_EMOJI)
    gid = str(ctx.guild.id)
    mid = str(msg.id)
    giveaway_db.setdefault(gid, {})[mid] = {
        "channel_id": ctx.channel.id,
        "prize":      prize,
        "winners":    winners,
        "host_id":    ctx.author.id,
        "end_ts":     end_ts,
        "ended":      False,
    }
    save_giveaways()
    await ctx.message.delete()

@bot.command()
@commands.has_permissions(manage_guild=True)
async def gend(ctx, message_id: int):
    """Terminer un giveaway immédiatement. Usage : +gend <message_id>"""
    gid = str(ctx.guild.id)
    mid = str(message_id)
    gdata = giveaway_db.get(gid, {}).get(mid)
    if not gdata:
        return await ctx.reply("❌ Giveaway introuvable.")
    if gdata.get("ended"):
        return await ctx.reply("❌ Ce giveaway est déjà terminé.")
    winners = await end_giveaway(ctx.guild, gdata["channel_id"], message_id)
    await ctx.reply(f"✅ Giveaway terminé avec **{len(winners) if winners else 0}** gagnant(s).")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def greroll(ctx, message_id: int):
    """Tirer un nouveau gagnant pour un giveaway terminé. Usage : +greroll <message_id>"""
    gid = str(ctx.guild.id)
    mid = str(message_id)
    gdata = giveaway_db.get(gid, {}).get(mid)
    if not gdata:
        return await ctx.reply("❌ Giveaway introuvable.")
    if not gdata.get("ended"):
        return await ctx.reply("❌ Ce giveaway n'est pas encore terminé.")
    channel = ctx.guild.get_channel(gdata["channel_id"])
    if not channel:
        return await ctx.reply("❌ Salon introuvable.")
    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return await ctx.reply("❌ Message introuvable.")
    reaction = discord.utils.get(message.reactions, emoji=GIVEAWAY_EMOJI)
    participants = []
    if reaction:
        async for user in reaction.users():
            if not user.bot:
                participants.append(user)
    if not participants:
        return await ctx.reply("😢 Aucun participant valide pour le reroll.")
    winner = random.choice(participants)
    await ctx.send(f"🎉 Nouveau gagnant du giveaway : {winner.mention} ! Félicitations pour **{gdata['prize']}** !")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def glist(ctx):
    """Lister les giveaways actifs sur ce serveur."""
    gid = str(ctx.guild.id)
    actifs = {mid: g for mid, g in giveaway_db.get(gid, {}).items() if not g.get("ended")}
    if not actifs:
        return await ctx.reply("ℹ️ Aucun giveaway actif.")
    e = success_embed("🎉 Giveaways actifs", "")
    for mid, g in actifs.items():
        channel = ctx.guild.get_channel(g["channel_id"])
        ch_mention = channel.mention if channel else "Salon supprimé"
        e.add_field(
            name=f"🎁 {g['prize']}",
            value=f"ID : `{mid}`\nSalon : {ch_mention}\nFin : <t:{g['end_ts']}:R>\nGagnants : {g['winners']}",
            inline=False
        )
    await ctx.send(embed=e)

# ─────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────
_start_time = datetime.now(timezone.utc)

@bot.command()
async def ping(ctx):
    """Afficher la latence du bot."""
    latency = round(bot.latency * 1000)
    color = discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 200 else discord.Color.red()
    e = discord.Embed(title="🏓 Pong !", color=color)
    e.add_field(name="Latence API", value=f"**{latency}ms**", inline=True)
    e.add_field(name="Statut", value="🟢 En ligne", inline=True)
    await ctx.send(embed=e)

@bot.command()
async def uptime(ctx):
    """Afficher le temps d'activité du bot."""
    delta = datetime.now(timezone.utc) - _start_time
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    days = hours // 24
    hours = hours % 24
    uptime_str = f"{days}j {hours}h {minutes}m {seconds}s"
    e = info_embed("⏱️ Uptime", f"Le bot est en ligne depuis **{uptime_str}**.\n**Démarré :** <t:{int(_start_time.timestamp())}:R>")
    await ctx.send(embed=e)

@bot.command()
async def calc(ctx, *, expression: str):
    """Calculatrice simple. Usage : +calc 2 + 2 * 10
    
    Supporte : + - * / ** // %
    """
    # Sécurité : uniquement chiffres et opérateurs mathématiques de base
    safe_expr = re.sub(r"[^0-9\s\+\-\*\/\.\(\)\%]", "", expression)
    if not safe_expr.strip():
        return await ctx.reply("❌ Expression invalide.")
    try:
        result = eval(safe_expr, {"__builtins__": {}})
        e = success_embed("🧮 Calculatrice", f"**Expression :** `{safe_expr.strip()}`\n**Résultat :** `{result}`")
        await ctx.send(embed=e)
    except ZeroDivisionError:
        await ctx.reply("❌ Division par zéro impossible.")
    except Exception:
        await ctx.reply("❌ Expression invalide.")

@bot.command()
async def coinflip(ctx):
    """Lancer une pièce — Pile ou Face."""
    result = random.choice(["🪙 **Pile !**", "🪙 **Face !**"])
    e = info_embed("🪙 Pile ou Face", result)
    await ctx.send(embed=e)

@bot.command()
async def roll(ctx, dice: str = "1d6"):
    """Lancer des dés. Usage : +roll [NdN]
    
    Exemples :
      +roll        → 1d6 par défaut
      +roll 2d6    → 2 dés à 6 faces
      +roll 1d20   → 1 dé à 20 faces
    """
    m = re.fullmatch(r"(\d+)d(\d+)", dice.lower())
    if not m:
        return await ctx.reply("❌ Format invalide. Exemple : `2d6`, `1d20`.")
    nb, faces = int(m.group(1)), int(m.group(2))
    if nb < 1 or nb > 20 or faces < 2 or faces > 100:
        return await ctx.reply("❌ Entre 1 et 20 dés, 2 à 100 faces.")
    results = [random.randint(1, faces) for _ in range(nb)]
    total = sum(results)
    rolls_str = " + ".join(str(r) for r in results) if nb > 1 else str(results[0])
    desc = f"🎲 **{dice}** → {rolls_str}" + (f" = **{total}**" if nb > 1 else "")
    e = info_embed("🎲 Lancer de dés", desc)
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def say(ctx, *, message: str):
    """Faire parler le bot dans le salon. Usage : +say <message>"""
    await ctx.message.delete()
    await ctx.send(message)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def poll(ctx, *, contenu: str):
    """Créer un sondage. Usage : +poll Question | Option1 | Option2 | ...
    
    Exemples :
      +poll Quelle couleur ? | Rouge | Bleu | Vert
      +poll Pizza ou Burger ? | 🍕 Pizza | 🍔 Burger
    """
    parts = [p.strip() for p in contenu.split("|")]
    if len(parts) < 3:
        return await ctx.reply("❌ Format : `+poll Question | Option1 | Option2 | ...`")
    if len(parts) > 11:
        return await ctx.reply("❌ Maximum 10 options.")
    question = parts[0]
    options  = parts[1:]
    emojis   = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    desc = "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(options))
    e = info_embed(f"📊 {question}", desc)
    e.set_footer(text=f"Sondage créé par {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.message.delete()
    msg = await ctx.send(embed=e)
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])

@bot.command()
async def remind(ctx, duration: str, *, message: str):
    """Te rappeler quelque chose après un délai. Usage : +remind <durée> <message>
    
    Exemples :
      +remind 30m Aller boire de l'eau
      +remind 2h Regarder le stream
    """
    delta = parse_duration(duration)
    if not delta:
        return await ctx.reply("❌ Durée invalide. Exemples : `30m`, `1h`, `2d`.")
    end_ts = int((datetime.now(timezone.utc) + delta).timestamp())
    e = info_embed("⏰ Rappel créé", f"Je te rappellerai <t:{end_ts}:R> :\n**{message}**")
    await ctx.reply(embed=e)

    async def do_remind():
        await asyncio.sleep(delta.total_seconds())
        e2 = warning_embed("⏰ Rappel !", f"{ctx.author.mention}, tu voulais te souvenir de :\n**{message}**")
        try:
            await ctx.send(embed=e2)
        except Exception:
            try:
                await ctx.author.send(embed=e2)
            except Exception:
                pass

    asyncio.ensure_future(do_remind())

@bot.command()
@commands.has_permissions(manage_messages=True)
async def embed(ctx, *, contenu: str):
    """Envoyer un embed personnalisé. Usage : +embed Titre | Description"""
    parts = contenu.split("|", 1)
    if len(parts) < 2:
        return await ctx.reply("❌ Format : `+embed Titre | Description`")
    titre, description = parts[0].strip(), parts[1].strip()
    e = info_embed(titre, description)
    e.set_footer(text=f"Par {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.message.delete()
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def announce(ctx, *, message: str):
    """Faire une annonce en embed dans le salon courant. Usage : +announce <message>"""
    e = discord.Embed(
        title="📢 Annonce",
        description=message,
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    e.set_footer(text=f"Par {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.message.delete()
    await ctx.send("@everyone", embed=e)

# ─────────────────────────────────────────
#  MEMBRES & RÔLES
# ─────────────────────────────────────────
@bot.command()
async def avatar(ctx, member: discord.Member = None):
    """Afficher l'avatar d'un membre. Usage : +avatar [@membre]"""
    member = member or ctx.author
    e = discord.Embed(title=f"🖼️ Avatar de {member}", color=member.color)
    e.set_image(url=member.display_avatar.url)
    e.add_field(name="Lien direct", value=f"[Ouvrir]({member.display_avatar.url})")
    await ctx.send(embed=e)

@bot.command()
@commands.has_permissions(manage_roles=True)
@commands.bot_has_permissions(manage_roles=True)
async def addrole(ctx, member: discord.Member, role: discord.Role):
    """Donner un rôle à un membre. Usage : +addrole @membre @rôle"""
    if role in member.roles:
        return await ctx.reply(f"❌ {member.mention} a déjà le rôle {role.mention}.")
    await member.add_roles(role, reason=f"Ajouté par {ctx.author}")
    e = success_embed("✅ Rôle ajouté", f"**Membre :** {member.mention}\n**Rôle :** {role.mention}\n**Modérateur :** {ctx.author.mention}")
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(manage_roles=True)
@commands.bot_has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, role: discord.Role):
    """Retirer un rôle d'un membre. Usage : +removerole @membre @rôle"""
    if role not in member.roles:
        return await ctx.reply(f"❌ {member.mention} n'a pas le rôle {role.mention}.")
    await member.remove_roles(role, reason=f"Retiré par {ctx.author}")
    e = mod_embed("✅ Rôle retiré", f"**Membre :** {member.mention}\n**Rôle :** {role.mention}\n**Modérateur :** {ctx.author.mention}", discord.Color.orange())
    await ctx.send(embed=e)
    await send_log(ctx.guild, e)

@bot.command()
@commands.has_permissions(administrator=True)
async def autorole(ctx, role: discord.Role):
    """Définir le rôle automatiquement donné aux nouveaux membres. Usage : +autorole @rôle"""
    cfg = get_guild_cfg(ctx.guild.id)
    cfg["auto_role"] = role.id
    save_config()
    await ctx.reply(f"✅ Auto-rôle défini sur {role.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setwelcome(ctx, channel: discord.TextChannel, *, message: str):
    """Configurer le message de bienvenue. Usage : +setwelcome #salon <message>
    
    Variables : {mention}, {name}, {server}
    """
    cfg = get_guild_cfg(ctx.guild.id)
    cfg["welcome_channel"] = channel.id
    cfg["welcome_message"]  = message
    save_config()
    preview = (message
               .replace("{mention}", ctx.author.mention)
               .replace("{server}", ctx.guild.name)
               .replace("{name}", str(ctx.author)))
    e = success_embed("✅ Message de bienvenue configuré", "")
    e.add_field(name="Salon", value=channel.mention, inline=True)
    e.add_field(name="Aperçu", value=preview, inline=False)
    await ctx.send(embed=e)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    """Afficher les informations d'un membre."""
    member = member or ctx.author
    roles  = [r.mention for r in member.roles if r != ctx.guild.default_role]
    gid, uid = str(ctx.guild.id), str(member.id)
    warn_count = len(warns_db.get(gid, {}).get(uid, []))
    e = discord.Embed(title=f"👤 {member}", color=member.color, timestamp=datetime.now(timezone.utc))
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="ID",          value=member.id,                                    inline=True)
    e.add_field(name="Surnom",      value=member.nick or "Aucun",                       inline=True)
    e.add_field(name="Bot",         value="✅" if member.bot else "❌",                  inline=True)
    e.add_field(name="Compte créé", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="A rejoint",   value=f"<t:{int(member.joined_at.timestamp())}:R>",  inline=True)
    e.add_field(name="⚠️ Warns",   value=str(warn_count),                              inline=True)
    e.add_field(name=f"Rôles ({len(roles)})", value=" ".join(roles) if roles else "Aucun", inline=False)
    if member.timed_out_until and member.timed_out_until > datetime.now(timezone.utc):
        e.add_field(name="🔇 Muet jusqu'à", value=f"<t:{int(member.timed_out_until.timestamp())}:R>", inline=False)
    await ctx.send(embed=e)

@bot.command()
async def whois(ctx, member: discord.Member = None):
    """Alias de userinfo. Usage : +whois [@membre]"""
    ctx.command = bot.get_command("userinfo")
    await ctx.invoke(bot.get_command("userinfo"), member=member)

@bot.command()
async def serverinfo(ctx):
    """Afficher les informations du serveur."""
    g = ctx.guild
    e = info_embed(f"🏠 {g.name}", "")
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    e.add_field(name="ID",             value=g.id,                                         inline=True)
    e.add_field(name="Propriétaire",   value=g.owner.mention,                              inline=True)
    e.add_field(name="Membres",        value=g.member_count,                               inline=True)
    e.add_field(name="Salons texte",   value=len(g.text_channels),                         inline=True)
    e.add_field(name="Salons vocaux",  value=len(g.voice_channels),                        inline=True)
    e.add_field(name="Rôles",          value=len(g.roles),                                 inline=True)
    e.add_field(name="Emojis",         value=f"{len(g.emojis)}/{g.emoji_limit}",           inline=True)
    e.add_field(name="Niveau boost",   value=f"⭐ Niveau {g.premium_tier}",                inline=True)
    e.add_field(name="Boosts",         value=g.premium_subscription_count or 0,            inline=True)
    e.add_field(name="Créé le",        value=f"<t:{int(g.created_at.timestamp())}:F>",     inline=False)
    if g.banner:
        e.set_image(url=g.banner.url)
    await ctx.send(embed=e)

@bot.command()
async def roleinfo(ctx, role: discord.Role):
    """Afficher les informations d'un rôle. Usage : +roleinfo @rôle"""
    perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
    e = discord.Embed(title=f"🏷️ Rôle : {role.name}", color=role.color, timestamp=datetime.now(timezone.utc))
    e.add_field(name="ID",          value=role.id,                      inline=True)
    e.add_field(name="Couleur",     value=str(role.color),              inline=True)
    e.add_field(name="Membres",     value=len(role.members),            inline=True)
    e.add_field(name="Mentionnable",value="✅" if role.mentionable else "❌", inline=True)
    e.add_field(name="Hoisted",     value="✅" if role.hoist else "❌", inline=True)
    e.add_field(name="Créé le",     value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True)
    if perms:
        e.add_field(name=f"Permissions ({len(perms)})", value=", ".join(perms[:15]) + ("..." if len(perms) > 15 else ""), inline=False)
    await ctx.send(embed=e)

# ─────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────
@bot.command()
@commands.has_permissions(administrator=True)
async def setlog(ctx, channel: discord.TextChannel):
    """Définir le salon de logs de modération."""
    cfg = get_guild_cfg(ctx.guild.id)
    cfg["log_channel"] = channel.id
    save_config()
    await ctx.reply(f"✅ Salon de logs défini sur {channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setmuterole(ctx, role: discord.Role):
    """Définir le rôle utilisé pour les mutes manuels (legacy)."""
    cfg = get_guild_cfg(ctx.guild.id)
    cfg["mute_role"] = role.id
    save_config()
    await ctx.reply(f"✅ Rôle mute défini sur {role.mention}.")

# ─────────────────────────────────────────
#  Lancement
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
