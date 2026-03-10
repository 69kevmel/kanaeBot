import logging
import time
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
                # (1477651520878280914 est l'ID de ton salon casino)
                casino_channel = member.guild.get_channel(1477651520878280914) 
                
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

    
async def refresh_event_message(bot: discord.Client):
    """Met à jour le panneau d'affichage avec une vision stricte sur 2 semaines."""

    event_channel_id = getattr(config, "EVENT_CHANNEL_ID", None)
    event_message_id = getattr(config, "EVENT_MESSAGE_ID", None)
    
    if not event_channel_id or not event_message_id:
        return

    channel = bot.get_channel(event_channel_id)
    if not channel:
        return
        
    try:
        msg = await channel.fetch_message(event_message_id)
    except discord.NotFound:
        return 
        
    db_events = await database.get_public_events(database.db_pool)
    try:
        discord_events = await msg.guild.fetch_scheduled_events()
    except Exception:
        discord_events = []

    unified_events = []
    db_event_ids = set()
    
    try:
        tz = zoneinfo.ZoneInfo("Europe/Paris")
    except Exception:
        tz = timezone(timedelta(hours=1))

    # A) Traitement BDD
    for d, heure_str, anim_id, titre, desc, event_id in db_events:
        if event_id: db_event_ids.add(event_id)
        
        h, m = 0, 0
        match = re.search(r"(\d{1,2})(?:[hH:](\d{2}))?", heure_str)
        if match:
            h = int(match.group(1))
            m = int(match.group(2)) if match.group(2) else 0
            
        start_dt = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m, tzinfo=tz)
        unified_events.append({"titre": titre, "desc": desc, "anim_id": anim_id, "start_dt": start_dt, "event_id": event_id})

    # B) Traitement Discord Manuel
    for e in discord_events:
        if e.id not in db_event_ids:
            unified_events.append({"titre": e.name, "desc": e.description or "*Pas de description.*", "anim_id": e.creator_id, "start_dt": e.start_time.astimezone(tz), "event_id": e.id})

    # 2. Tri chronologique
    unified_events.sort(key=lambda x: x["start_dt"])

    # 🕒 Préparation des dates
    now_ts = int(time.time())
    now_dt = datetime.now(tz)
    today = now_dt.date()
    today_iso = today.isocalendar()[:2]
    next_week_iso = (today + timedelta(days=7)).isocalendar()[:2]
    two_weeks_limit = today + timedelta(days=14) # 🛡️ LA LIMITE DES 14 JOURS
    jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    # 🛡️ SÉCURITÉ : On ne garde que les events des 14 prochains jours
    visible_events = []
    hidden_count = 0
    
    for ev in unified_events:
        if ev["start_dt"].date() <= two_weeks_limit:
            visible_events.append(ev)
        else:
            hidden_count += 1

    categories = {
        1: {"name": "🔥 IMMINENT", "color": discord.Color.brand_red(), "events": []},
        2: {"name": "📅 CETTE SEMAINE", "color": discord.Color.gold(), "events": []},
        3: {"name": "🚀 SEMAINE PROCHAINE", "color": discord.Color.blue(), "events": []},
        4: {"name": "📆 PLUS TARD", "color": discord.Color.dark_grey(), "events": []}
    }

    # On utilise maintenant la liste filtrée
    for ev in visible_events:
        start = ev["start_dt"]
        ev_date = start.date()
        days_diff = (ev_date - today).days
        ev_iso = ev_date.isocalendar()[:2]
        
        if days_diff == 0: ev["jour_str"] = "Aujourd'hui"
        elif days_diff == 1: ev["jour_str"] = "Demain"
        else: ev["jour_str"] = f"{jours_fr[start.weekday()]} {start.strftime('%d/%m')}"
        
        ev["heure_str"] = start.strftime("%Hh%M").replace("h00", "h")
        
        if days_diff <= 2: categories[1]["events"].append(ev)
        elif ev_iso == today_iso: categories[2]["events"].append(ev)
        elif ev_iso == next_week_iso: categories[3]["events"].append(ev)
        else: categories[4]["events"].append(ev)

    embeds = []

    # Le Header Principal
    desc_header = "*Vision sur les 14 prochains jours de l'agenda Kanaé.* 💨\n\n" f"📡 **Synchro :** <t:{now_ts}:R>"
    
    # On indique combien d'événements sont cachés car prévus dans + de 2 semaines
    if hidden_count > 0:
        s = "s" if hidden_count > 1 else ""
        desc_header += f"\n*(+{hidden_count} autre{s} événement{s} prévu{s} plus tard)*"

    main_embed = discord.Embed(
        title="📅 L'AGENDA DES EVENTS KANAÉ", 
        description=desc_header,
        color=discord.Color.dark_theme()
    )
    main_embed.set_thumbnail(url=bot.user.display_avatar.url)
    embeds.append(main_embed)

    if not visible_events:
        main_embed.add_field(name="📭 Écran Vide...", value="*L'équipe prépare du lourd !* 🌿", inline=False)
        main_embed.set_footer(text="🟢 Mis à jour automatiquement", icon_url="https://i.imgur.com/8Q5A40b.gif")
    else:
        for cat_id, cat_data in categories.items():
            if not cat_data["events"]:
                continue 
                
            cat_embed = discord.Embed(title=f"─── {cat_data['name']} ───", color=cat_data["color"])
            
            for ev in cat_data["events"]:
                anim_text = f"🎤 **Animé par :** <@{ev['anim_id']}>\n" if ev['anim_id'] else ""
                event_link = ""
                if ev["event_id"]:
                    url = f"https://discord.com/events/{msg.guild.id}/{ev['event_id']}"
                    event_link = f"\n\n> 📥 **[REJOINDRE L'ÉVÉNEMENT (CLIQUE ICI)]({url})**"
                
                # 🛡️ SÉCURITÉ : Raccourcir la description si elle est trop longue
                safe_desc = ev['desc']
                if len(safe_desc) > 150:
                    safe_desc = safe_desc[:147] + "..."
                
                val = f"### {ev['titre'].upper()}\n{anim_text}📝 *{safe_desc}*{event_link}\n\u200b"
                
                cat_embed.add_field(name=f"📍 {ev['jour_str']} • {ev['heure_str']}", value=val, inline=False)
            
            embeds.append(cat_embed)
            
        embeds[-1].set_footer(text="🟢 Mis à jour automatiquement", icon_url="https://i.imgur.com/8Q5A40b.gif")

    await msg.edit(content="", embeds=embeds[:10], view=None)