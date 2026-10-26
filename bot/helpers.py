import logging
import discord
import aiohttp

import re
import zoneinfo
from datetime import datetime, timezone, timedelta

from . import config, database

logger = logging.getLogger(__name__)

async def safe_send_dm(user: discord.User, content: str):
    if len(content) > 2000:
        content = content[:1990] + "…"
    content = content.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
    try:
        await user.send(content)
        logger.info("DM sent to %s", user)
    except discord.HTTPException as e:
        logger.warning("Failed to send DM to %s: %s", user, e)


async def get_top_scores(guild: discord.Guild, limit: int = 5):
    async with database.db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, points FROM scores ORDER BY points DESC;"
            )
            all_rows = await cur.fetchall()

    top_filtered = []
    for uid, pts in all_rows:
        member = guild.get_member(int(uid))
        if member and any(role.id == config.EXCLUDED_ROLE_ID for role in member.roles):
            continue
        top_filtered.append((uid, pts))
        if len(top_filtered) >= limit:
            break
    return top_filtered


async def build_top5_message(
    bot: discord.Client,
    guild: discord.Guild,
    *,
    mention_users: bool,
    header: str,
) -> str | None:
    scores = await get_top_scores(guild, 5)
    if not scores:
        return None

    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [header]
    for idx, (uid, pts) in enumerate(scores):
        user = await bot.fetch_user(int(uid))
        name = user.mention if mention_users else user.display_name
        lines.append(f"{icons[idx]} {name} \u2192 {pts} pts")
    return "\n".join(lines)

async def update_member_prestige_role(member: discord.Member, points: int):
    """Gère les changements de grade (montée/descente) avec messages adaptés."""
    # Sécurité 1 : On s'assure que c'est bien un membre d'un serveur et pas un message privé
    if not isinstance(member, discord.Member):
        return
    # 1. Trouver le grade cible basé sur les points actuels
    target_role_id = None
    target_threshold = 0
    for threshold in sorted(config.PRESTIGE_ROLES.keys(), reverse=True):
        if points >= threshold:
            target_role_id = config.PRESTIGE_ROLES[threshold]
            target_threshold = threshold
            break

    if not target_role_id:
        return # Aucun palier atteint, on ne fait rien silencieusement

    target_role = member.guild.get_role(target_role_id)
    if not target_role:
        logger.error(f"❌ [Prestige] Le rôle ID {target_role_id} est introuvable sur le serveur {member.guild.name}.")
        return

    # 2. Identifier les rôles de prestige actuels du membre
    all_prestige_ids = set(config.PRESTIGE_ROLES.values())
    current_prestige_roles = [r for r in member.roles if r.id in all_prestige_ids]

    # Sécurité 2 : Opti API -> S'il a DÉJÀ le bon rôle et AUCUN autre rôle de prestige, on stop
    if len(current_prestige_roles) == 1 and current_prestige_roles[0].id == target_role_id:
        return

    # Sécurité 3 : Le bot a-t-il la permission de gérer les rôles ?
    if not member.guild.me.guild_permissions.manage_roles:
        logger.warning(f"⚠️ [Prestige] Il me manque la permission 'Gérer les rôles' sur {member.guild.name}.")
        return

    # Sécurité 4 : Le bot est-il placé assez haut dans la liste des rôles ?
    if target_role.position >= member.guild.me.top_role.position:
        logger.warning(f"⚠️ [Prestige] Le rôle {target_role.name} est au-dessus du mien. Je ne peux pas le donner.")
        return

    # 3. Déterminer s'il s'agit d'une promotion ou d'une rétrogradation
    is_promotion = True
    old_role_name = "Inconnu"
    old_role = None
    
    if current_prestige_roles:
        # On crée un dictionnaire inverse {role_id: points} pour comparer les paliers
        id_to_threshold = {v: k for k, v in config.PRESTIGE_ROLES.items()}
        # On prend le premier rôle de prestige qu'il possède
        old_role = current_prestige_roles[0]
        old_role_name = old_role.name
        old_threshold = id_to_threshold.get(old_role.id, 0)
        
        # Si le nouveau seuil est inférieur à l'ancien, c'est une rétrogradation
        if target_threshold < old_threshold:
            is_promotion = False

    # 4. Préparer la liste des rôles à enlever (tous sauf le nouveau)
    roles_to_remove = [r for r in current_prestige_roles if r.id != target_role_id]

    try:
        # Nettoyage des anciens rôles
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Nettoyage anciens paliers Kanaé")
            logger.info(f"🧹 [Prestige] Anciens rôles retirés pour {member.display_name}.")

        # Ajout du nouveau rôle
        if target_role not in member.roles:
            await member.add_roles(target_role, reason=f"Nouveau palier Kanaé : {points} pts")
            logger.info(f"🏆 [Prestige] {member.display_name} vient de passer au rang {target_role.name} ({points} pts) !")
            
        # 5. Gestion des annonces (Public / MP)
        public_channel = member.guild.get_channel(config.BLABLA_CHANNEL_ID)
        
        if is_promotion:
            # --- VÉRIFICATION ANTI-SPAM MONTÉE ---
            already_unlocked = False
            async with database.db_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1 FROM prestige_unlocks WHERE user_id=%s AND role_id=%s;", (member.id, target_role_id))
                    if await cur.fetchone():
                        already_unlocked = True
                    else:
                        await cur.execute("INSERT INTO prestige_unlocks (user_id, role_id) VALUES (%s, %s);", (member.id, target_role_id))
            
            if not already_unlocked:
                # 1ère fois qu'il atteint ce rôle : Grosse Annonce + MP
                msg_dm = f"✨ **FÉLICITATIONS FRÉROT !** ✨\n\nTu as franchi un cap avec **{points} points** ! Tu es maintenant : **{target_role.name}** 👑\nContinue comme ça, la légende est en marche ! 🌿🔥"
                await safe_send_dm(member, msg_dm)
                
                if public_channel:
                    announcement = (
                        f"🎉 **ALERTE PRESTIGE !** 🎉\n\n"
                        f"Félicitations à {member.mention} qui grimpe en grade et devient officiellement : **{target_role.name}** 👑\n"
                    )
                    # Sécurité maximale : on autorise le ping du membre, mais on bloque strictement les rôles
                    await public_channel.send(
                        announcement,
                        allowed_mentions=discord.AllowedMentions(roles=False, users=True)
                    )
        
        else:
            # --- VÉRIFICATION ANTI-SPAM DESCENTE ---
            already_demoted = False
            if old_role:
                async with database.db_pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        # On enregistre l'ID du rôle qu'il vient de PERDRE (old_role.id)
                        await cur.execute("SELECT 1 FROM prestige_demotions WHERE user_id=%s AND role_id=%s;", (member.id, old_role.id))
                        if await cur.fetchone():
                            already_demoted = True
                        else:
                            await cur.execute("INSERT INTO prestige_demotions (user_id, role_id) VALUES (%s, %s);", (member.id, old_role.id))

            if not already_demoted:
                # 1ère fois qu'il perd ce rôle : Message triste envoyé DANS LE CASINO !
                # (CASINO_CHANNEL_ID est défini dans config.py)
                casino_channel = member.guild.get_channel(config.CASINO_CHANNEL_ID)

                if casino_channel:
                    import random
                    # On utilise .name au lieu de .mention pour éviter le ping des rôles
                    new_role_name = target_role.name 
                    
                    sad_messages = [
                        f"📉 **COUP DUR...** {member.mention} vient de perdre son rang de **{old_role_name}** et redescend au rang de **{new_role_name}**. La roue tourne, courage frérot... 🕯️🌿",
                        f"Aïe... {member.mention} a trop joué avec le feu. Il n'est plus **{old_role_name}** et redevient simple **{new_role_name}**. On t'envoie de la force ! 📉💨",
                        f"La descente est brutale pour {member.mention}. Adieu le grade **{old_role_name}**, retour au rang de **{new_role_name}**. On remonte la pente bientôt ? 📉🕯️"
                    ]
                    
                    # On ajoute explicitement allowed_mentions pour être sûr à 100% qu'aucun rôle n'est notifié
                    await casino_channel.send(
                        random.choice(sad_messages), 
                        allowed_mentions=discord.AllowedMentions(roles=False, users=True)
                    )

    except discord.Forbidden:
        logger.error(f"⛔ [Prestige] Discord me refuse l'accès aux rôles de {member.display_name} (est-il propriétaire ou admin plus haut que moi ?).")
    except Exception as e:
        logger.error(f"❌ [Prestige] Erreur inattendue pour {member.display_name} : {e}")

    
_event_panel_lock = None
_event_panel_overrides = {}


def build_event_embeds(bot, guild, db_events, discord_events, now=None):
    """Build a panel within Discord's 6000-character message-wide limit."""
    try:
        tz = zoneinfo.ZoneInfo("Europe/Paris")
    except zoneinfo.ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=1))
    now = now or datetime.now(tz)
    today = now.astimezone(tz).date()
    unified = []
    database_ids = set()
    for d, heure, anim_id, titre, desc, event_id in db_events:
        match = re.fullmatch(r"\s*(\d{1,2})(?:[hH:](\d{2})?)?\s*", str(heure))
        try:
            if not match:
                raise ValueError("heure non reconnue")
            start = datetime.combine(d, datetime.min.time()).replace(
                hour=int(match[1]), minute=int(match[2] or 0), tzinfo=tz,
            )
        except (TypeError, ValueError):
            logger.warning("Créneau ignoré dans le panneau : date/heure invalide (%s, %s)", d, heure)
            continue
        unified.append((start, titre or "Sans titre", desc or "Pas de description.", anim_id, event_id))
        if event_id:
            database_ids.add(event_id)
    for event in discord_events:
        if event.id not in database_ids and event.status in (discord.EventStatus.scheduled, discord.EventStatus.active):
            unified.append((event.start_time.astimezone(tz), event.name, event.description or "Pas de description.", event.creator_id, event.id))
    unified.sort(key=lambda row: row[0])
    visible = [row for row in unified if today <= row[0].date() <= today + timedelta(days=14)]
    hidden = sum(row[0].date() > today + timedelta(days=14) for row in unified)
    header = "*Vision sur les 14 prochains jours de l'agenda Kanaé.* 💨\n\n"
    header += f"📡 **Synchro :** <t:{int(now.timestamp())}:R>"
    if hidden:
        header += f"\n*(+{hidden} événement(s) prévu(s) plus tard)*"
    main = discord.Embed(title="📅 L'AGENDA DES EVENTS KANAÉ", description=header, color=discord.Color.dark_theme())
    if bot.user:
        main.set_thumbnail(url=bot.user.display_avatar.url)
    embeds = [main]
    categories = [
        ("🔥 IMMINENT", discord.Color.brand_red(), []),
        ("📅 CETTE SEMAINE", discord.Color.gold(), []),
        ("🚀 SEMAINE PROCHAINE", discord.Color.blue(), []),
        ("📆 PLUS TARD", discord.Color.dark_grey(), []),
    ]
    today_iso = today.isocalendar()[:2]
    next_iso = (today + timedelta(days=7)).isocalendar()[:2]
    for row in visible:
        start = row[0]
        if (start.date() - today).days <= 2:
            index = 0
        elif start.date().isocalendar()[:2] == today_iso:
            index = 1
        elif start.date().isocalendar()[:2] == next_iso:
            index = 2
        else:
            index = 3
        categories[index][2].append(row)
    omitted = 0
    jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    for name, color, rows in categories:
        category = discord.Embed(title=f"─── {name} ───", color=color)
        for start, titre, desc, anim_id, event_id in rows:
            days = (start.date() - today).days
            day = "Aujourd'hui" if days == 0 else "Demain" if days == 1 else f"{jours_fr[start.weekday()]} {start:%d/%m}"
            field_name = f"📍 {day} • {start:%Hh%M}"
            safe_desc = desc if len(desc) <= 150 else desc[:147] + "..."
            anim = f"🎤 **Animé par :** <@{anim_id}>\n" if anim_id else ""
            link = f"\n\n> 📥 **[REJOINDRE L'ÉVÉNEMENT (CLIQUE ICI)](https://discord.com/events/{guild.id}/{event_id})**" if event_id else ""
            value = f"— {titre[:100].upper()}\n{anim}📝 *{safe_desc}*{link}\n\u200b"
            value = value if len(value) <= 1024 else value[:1021] + "..."
            # Reserve room for a message-wide overflow notice and footer.
            size = sum(len(embed) for embed in embeds) + len(category) + len(field_name) + len(value)
            if len(category.fields) >= 25 or size > 5700:
                omitted += 1
                continue
            category.add_field(name=field_name, value=value, inline=False)
        if category.fields:
            embeds.append(category)
    if omitted:
        main.add_field(name="📚 Suite du planning", value=f"{omitted} événement(s) supplémentaire(s). Consulte `/planning` ou l'onglet Événements du serveur.", inline=False)
    if not visible:
        main.add_field(name="📭 Écran Vide...", value="*L'équipe prépare du lourd !* 🌿", inline=False)
    embeds[-1].set_footer(text="🟢 Mis à jour automatiquement")
    return embeds


async def refresh_event_message(bot: discord.Client):
    """Refresh the public panel, recreating and remembering a deleted message."""
    import asyncio
    global _event_panel_lock
    if _event_panel_lock is None:
        _event_panel_lock = asyncio.Lock()
    async with _event_panel_lock:
        channel_id = getattr(config, "EVENT_CHANNEL_ID", None)
        source_id = getattr(config, "EVENT_MESSAGE_ID", None)
        if not channel_id or not source_id:
            return None
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        permissions = channel.permissions_for(channel.guild.me)
        if not (permissions.view_channel and permissions.read_message_history and permissions.send_messages and permissions.embed_links):
            raise PermissionError("Le bot doit voir le salon du planning, lire son historique, y envoyer des messages et intégrer des liens.")
        key = (channel_id, source_id)
        message_id = _event_panel_overrides.get(key)
        if message_id is None:
            message_id = await database.get_event_panel_message_id(database.db_pool, channel_id, source_id)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            message = None
        if message is not None and message.author.id != bot.user.id:
            raise PermissionError("Le message configuré pour le planning n'appartient pas au bot.")
        db_events = await database.get_public_events(database.db_pool)
        # Preserve the existing panel when native events cannot be fetched.
        discord_events = await channel.guild.fetch_scheduled_events()
        embeds = build_event_embeds(bot, channel.guild, db_events, discord_events)
        if message is None:
            message = await channel.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
            _event_panel_overrides[key] = message.id
        else:
            await message.edit(content="", embeds=embeds, view=None, allowed_mentions=discord.AllowedMentions.none())
        if message.id != source_id:
            # Cache first, so a transient persistence error cannot create duplicates.
            _event_panel_overrides[key] = message.id
            await database.save_event_panel_message_id(database.db_pool, channel_id, source_id, message.id)
        return message
