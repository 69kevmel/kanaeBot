import logging
import discord
import aiohttp

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
    """Vérifie, nettoie les anciens grades et ajoute le nouveau palier de prestige avec logs et sécurités."""
    # Sécurité 1 : On s'assure que c'est bien un membre d'un serveur et pas un message privé
    #SUPPRIMER
    return
    if not isinstance(member, discord.Member):
        return

    # On trouve le grade correspondant
    target_role_id = None
    for threshold in sorted(config.PRESTIGE_ROLES.keys(), reverse=True):
        if points >= threshold:
            target_role_id = config.PRESTIGE_ROLES[threshold]
            break

    if not target_role_id:
        return # Aucun palier atteint, on ne fait rien silencieusement

    target_role = member.guild.get_role(target_role_id)
    if not target_role:
        logger.error(f"❌ [Prestige] Le rôle ID {target_role_id} est introuvable sur le serveur {member.guild.name}.")
        return

    # On liste tous les IDs des rôles de prestige pour comparer
    all_prestige_ids = set(config.PRESTIGE_ROLES.values())
    
    # On isole les rôles de prestige que le membre possède déjà
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

    # On prépare la liste des rôles à enlever (tous sauf le nouveau)
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
            
            # Message de félicitations
            msg = f"✨ **FÉLICITATIONS FRÉROT !** ✨\n\nTu viens de franchir un cap avec **{points} points** ! Tu as débloqué le grade : **{target_role.name}** 👑\nContinue comme ça, la légende est en marche ! 🌿🔥"
            await safe_send_dm(member, msg)

            # --- ANNONCE PUBLIQUE ---
        public_channel = member.guild.get_channel(config.BLABLA_CHANNEL_ID)
        if public_channel:
            # On choisit un petit emoji selon le prestige pour le fun
            announcement = (
                f"🎉 **ALERTE PRESTIGE !** 🎉\n\n"
                f"Félicitations à {member.mention} qui vient de franchir un cap monumental !\n"
                f"Il devient officiellement : **{target_role.name}** 👑\n"
            )
            await public_channel.send(announcement)

    except discord.Forbidden:
        logger.error(f"⛔ [Prestige] Discord me refuse l'accès aux rôles de {member.display_name} (est-il propriétaire ou admin plus haut que moi ?).")
    except Exception as e:
        logger.error(f"❌ [Prestige] Erreur inattendue pour {member.display_name} : {e}")