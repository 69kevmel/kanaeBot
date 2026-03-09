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
    """Met à jour le panneau d'affichage public des événements en temps réel."""

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
        return # Le message a été supprimé
        
    # 1. Récupération des events de la BDD
    db_events = await database.get_public_events(database.db_pool)
    
    # 2. Récupération des events Discord (Créés manuellement ou par le bot)
    try:
        discord_events = await msg.guild.fetch_scheduled_events()
    except Exception:
        discord_events = []

    # 3. Fusion et préparation au tri
    unified_events = []
    db_event_ids = set()
    
    # Gestion du fuseau horaire
    try:
        tz = zoneinfo.ZoneInfo("Europe/Paris")
    except Exception:
        tz = timezone(timedelta(hours=1))

    # A) Traitement des events de la BDD
    for d, heure_str, anim_id, titre, desc, event_id in db_events:
        if event_id:
            db_event_ids.add(event_id)
        
        h, m = 0, 0
        match = re.search(r"(\d{1,2})(?:[hH:](\d{2}))?", heure_str)
        if match:
            h = int(match.group(1))
            m = int(match.group(2)) if match.group(2) else 0
            
        naive_dt = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)
        start_dt = naive_dt.replace(tzinfo=tz)
        
        unified_events.append({
            "titre": titre,
            "desc": desc,
            "anim_id": anim_id,
            "start_dt": start_dt,
            "event_id": event_id
        })

    # B) Ajout des events créés MANUELLEMENT sur Discord
    for e in discord_events:
        if e.id not in db_event_ids:
            unified_events.append({
                "titre": e.name,
                "desc": e.description or "*Aucune description fournie.*",
                "anim_id": e.creator_id,
                "start_dt": e.start_time.astimezone(tz),
                "event_id": e.id
            })

    # 4. Tri Magique : Du plus proche au plus éloigné !
    unified_events.sort(key=lambda x: x["start_dt"])

    # 🕒 Calculs des dates pour les séparateurs intelligents
    now_ts = int(time.time())
    now_dt = datetime.now(tz)
    today = now_dt.date()
    
    today_iso = today.isocalendar()[:2] # Récupère (Année, Semaine_n°)
    next_week_iso = (today + timedelta(days=7)).isocalendar()[:2]
    
    # 🎨 Construction de l'Embed
    embed = discord.Embed(
        title="📅 L'AGENDA DES EVENTS KANAÉ", 
        description=(
            "*Toutes les soirées, events et animations en un clin d'œil.* 💨\n\n"
            f"📡 **Synchro en direct :** <t:{now_ts}:R>"
        ),
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)

    if not unified_events:
        embed.add_field(
            name="📭 Écran Vide...",
            value="*L'équipe prépare du lourd en coulisses, restez à l'écoute !* 🌿",
            inline=False
        )
    else:
        jours_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        current_cat_id = 0
        field_count = 0 # Sécurité : Discord bloque à 25 "champs" par message
        
        for ev in unified_events:
            if field_count >= 24:
                break # On arrête s'il y a trop d'events d'un coup
                
            start = ev["start_dt"]
            ev_date = start.date()
            days_diff = (ev_date - today).days
            months_diff = (ev_date.year - today.year) * 12 + ev_date.month - today.month
            
            # --- 1. FORMATAGE DE LA DATE (Aujourd'hui, Demain...) ---
            if days_diff == 0:
                jour_str = "Aujourd'hui"
            elif days_diff == 1:
                jour_str = "Demain"
            elif days_diff == 2:
                jour_str = "Après-demain"
            else:
                jour_str = f"{jours_fr[start.weekday()]} {start.strftime('%d/%m')}"
                
            # --- 2. DÉTERMINATION DE LA CATÉGORIE (Pour les séparateurs) ---
            ev_iso = ev_date.isocalendar()[:2]
            
            if days_diff <= 2:
                cat_id = 1
                cat_name = "🔥 IMMINENT"
            elif ev_iso == today_iso:
                cat_id = 2
                cat_name = "📅 CETTE SEMAINE"
            elif ev_iso == next_week_iso:
                cat_id = 3
                cat_name = "🚀 SEMAINE PROCHAINE"
            elif months_diff == 1:
                cat_id = 4
                cat_name = "📆 MOIS PROCHAIN"
            else:
                cat_id = 5
                cat_name = "🔮 PLUS TARD"
                
            # --- 3. AJOUT DU SÉPARATEUR SI ON CHANGE DE CATÉGORIE ---
            if cat_id != current_cat_id:
                embed.add_field(
                    name=f"━━━ {cat_name} ━━━",
                    value="\u200b", # Code secret Discord : Espace invisible !
                    inline=False
                )
                current_cat_id = cat_id
                field_count += 1
                
                if field_count >= 24:
                    break # On revérifie au cas où le séparateur était le 25ème

            heure_str = start.strftime("%Hh%M").replace("h00", "h")
            anim_text = f"🎤 **Animé par :** <@{ev['anim_id']}>\n" if ev['anim_id'] else ""
            event_link = f"\n\n🔗 [**S'inscrire à l'Événement**](https://discord.com/events/{msg.guild.id}/{ev['event_id']})" if ev["event_id"] else ""
            
            # Ajout de l'événement en lui-même
            embed.add_field(
                name=f"🗓️ {jour_str} à {heure_str} • 🔥 {ev['titre'].upper()}",
                value=f"{anim_text}📝 {ev['desc']}{event_link}",
                inline=False
            )
            field_count += 1
            
    embed.set_footer(text="🟢 Panneau synchronisé automatiquement", icon_url="https://i.imgur.com/8Q5A40b.gif")
    
    await msg.edit(content="", embed=embed, view=None)