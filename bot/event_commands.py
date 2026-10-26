"""Staff commands for editing event content and repairing its public panel."""

import asyncio
import logging

import discord
from discord import app_commands

from . import config, database, helpers

logger = logging.getLogger(__name__)
_event_edit_lock = asyncio.Lock()


def _is_staff(interaction):
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(interaction.guild and permissions and (permissions.administrator or permissions.manage_messages))


async def _check_planning_guild(interaction):
    if not _is_staff(interaction):
        raise PermissionError("Cette commande est réservée au staff avec la permission Gérer les messages.")
    channel_id = getattr(config, "EVENT_CHANNEL_ID", None)
    if not channel_id:
        raise ValueError("Le salon du planning n'est pas configuré. Un administrateur doit utiliser `/setup-planning`.")
    channel = interaction.client.get_channel(channel_id)
    if channel is None:
        channel = await interaction.client.fetch_channel(channel_id)
    if getattr(channel, "guild", None) is None or channel.guild.id != interaction.guild.id:
        raise PermissionError("Le planning appartient à un autre serveur.")
    return channel


def _validate_content(titre, description):
    if titre is not None:
        titre = titre.strip()
        if not 1 <= len(titre) <= 100:
            raise ValueError("Le titre doit contenir entre 1 et 100 caractères.")
    if description is not None:
        description = description.strip()
        if not 1 <= len(description) <= 1000:
            raise ValueError("La description doit contenir entre 1 et 1000 caractères.")
    return titre, description


async def edit_planned_event(interaction, slot_id, titre=None, description=None):
    """Serialize edits through compensation, including after a failed commit."""
    async with _event_edit_lock:
        return await _edit_planned_event_locked(interaction, slot_id, titre, description)


async def _edit_planned_event_locked(interaction, slot_id, titre=None, description=None):
    """Edit in place and keep the reservation row locked until the DB commits."""
    titre, description = _validate_content(titre, description)
    native_event = None
    restore = {}
    native_edited = False
    try:
        async with database.locked_pro_slot(database.db_pool, slot_id) as (slot, cursor):
            if not slot or not slot[2]:
                raise ValueError("Ce créneau n'existe pas ou n'est plus réservé. Sélectionne-le dans la liste.")
            _, _, _, owner_id, old_title, old_description, event_id = slot
            if not interaction.user.guild_permissions.administrator and int(owner_id or 0) != interaction.user.id:
                raise PermissionError("Tu peux modifier uniquement tes propres événements. Un administrateur peut tous les modifier.")
            if titre is None and description is None:
                return False
            if not event_id:
                raise ValueError("Cette réservation n'a pas d'événement Discord associé. Demande au staff de la recréer avec `/annuler_resa` puis `/reserver`.")
            # Fetch fresh data: an event may have been changed manually since caching.
            native_event = await interaction.guild.fetch_scheduled_event(int(event_id))
            if native_event.status not in (discord.EventStatus.scheduled, discord.EventStatus.active):
                raise ValueError("Cet événement est terminé ou annulé et ne peut plus être modifié.")
            permissions = interaction.guild.me.guild_permissions
            owns_event = native_event.creator_id == interaction.client.user.id
            if not (permissions.manage_events or (getattr(permissions, "create_events", False) and owns_event)):
                raise PermissionError("Il me manque la permission Gérer les événements, ou Créer des événements pour un événement que j'ai créé.")
            changes = {}
            if titre is not None:
                changes["name"] = titre
                restore["name"] = native_event.name
            if description is not None:
                changes["description"] = description
                restore["description"] = native_event.description
            # Updating only name/description preserves dates, image, link and subscribers.
            await native_event.edit(**changes, reason=f"/modifier-event par {interaction.user.id}")
            native_edited = True
            await database.update_pro_slot_content(
                cursor, slot_id,
                titre if titre is not None else old_title,
                description if description is not None else old_description,
            )
        return True
    except BaseException:
        if native_edited:
            try:
                await native_event.edit(**restore, reason="Restauration après échec de la sauvegarde du planning")
            except Exception:
                logger.exception("Impossible de restaurer l'événement %s après échec DB", native_event.id)
        raise


async def _send_error(interaction, error):
    if isinstance(error, (PermissionError, ValueError)):
        message = str(error)
    elif isinstance(error, discord.NotFound):
        message = "L'événement ou le salon a été supprimé. Pour un événement supprimé, recrée la réservation avec `/annuler_resa` puis `/reserver`."
    elif isinstance(error, discord.Forbidden):
        message = "Discord refuse l'accès. Vérifie les permissions du bot sur le salon du planning et sur les événements."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Patiente encore {error.retry_after:.0f} seconde(s) avant de réessayer."
    else:
        logger.error("Échec d'une commande événement", exc_info=(type(error), error, error.__traceback__))
        message = "La modification n'a pas pu être terminée. Vérifie le planning avant de réessayer."
    if interaction.response.is_done():
        await interaction.followup.send(f"❌ {message}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)


def setup(bot):
    async def reserved_autocomplete(interaction: discord.Interaction, current: str):
        if not _is_staff(interaction):
            return []
        channel = interaction.client.get_channel(getattr(config, "EVENT_CHANNEL_ID", 0))
        if channel is None or channel.guild.id != interaction.guild.id:
            return []
        try:
            owner_id = None if interaction.user.guild_permissions.administrator else interaction.user.id
            # Une autocomplétion ne peut pas différer sa réponse Discord : on
            # réserve une marge sur le délai de trois secondes en cas de DB lente.
            rows = await asyncio.wait_for(
                database.get_reserved_pro_slots(database.db_pool, animateur_id=owner_id), timeout=2.0
            )
            choices = []
            for slot_id, day, hour, title in rows:
                label = f"#{slot_id} · {day:%d/%m/%Y} à {hour} · {title or 'Sans titre'}"[:100]
                if current.casefold() in label.casefold():
                    choices.append(app_commands.Choice(name=label, value=str(slot_id)))
            return choices[:25]
        except asyncio.TimeoutError:
            logger.warning("Autocomplétion des événements : délai de base de données dépassé")
            return []
        except Exception:
            logger.exception("Autocomplétion des événements indisponible")
            return []

    @bot.tree.command(name="modifier-event", description="(Staff) Corrige ton événement et actualise son message public")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda interaction: (interaction.guild_id, interaction.user.id))
    @app_commands.autocomplete(creneau=reserved_autocomplete)
    @app_commands.describe(
        creneau="Événement à corriger : choisis un créneau réservé dans la liste",
        titre="Nouveau titre, de 1 à 100 caractères ; laisse vide pour le conserver",
        description="Nouvelle description, de 1 à 1000 caractères ; laisse vide pour la conserver",
    )
    async def modifier_event(
        interaction: discord.Interaction,
        creneau: str,
        titre: app_commands.Range[str, 1, 100] = None,
        description: app_commands.Range[str, 1, 1000] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await _check_planning_guild(interaction)
            if not creneau.isdecimal() or not 0 < int(creneau) <= 2147483647:
                raise ValueError("Sélection invalide. Choisis un créneau dans la liste.")
            changed = await edit_planned_event(interaction, int(creneau), titre, description)
        except Exception as error:
            await _send_error(interaction, error)
            return
        try:
            panel = await helpers.refresh_event_message(interaction.client)
            if panel is None:
                raise ValueError("Le panneau n'est pas configuré. Demande à un administrateur d'utiliser `/setup-planning`.")
        except Exception as error:
            logger.warning("Événement sauvegardé mais panneau non actualisé : %s", error)
            status = "L'événement a été modifié." if changed else "Les informations de l'événement ont été conservées."
            await interaction.followup.send(f"⚠️ {status} Le panneau n'a pas pu être actualisé : vérifie les permissions, puis utilise `/actualiser-events`.", ephemeral=True)
            return
        status = "Événement modifié" if changed else "Informations conservées"
        await interaction.followup.send(f"✅ {status} et [panneau actualisé]({panel.jump_url}).", ephemeral=True)

    @bot.tree.command(name="actualiser-events", description="(Staff) Actualise le planning public et recrée son message s'il a été supprimé")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.cooldown(1, 15.0, key=lambda interaction: interaction.guild_id)
    async def actualiser_events(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await _check_planning_guild(interaction)
            panel = await helpers.refresh_event_message(interaction.client)
            if panel is None:
                raise ValueError("Le panneau n'est pas configuré. Un administrateur doit utiliser `/setup-planning`.")
            await interaction.followup.send(f"✅ [Panneau du planning actualisé]({panel.jump_url}).", ephemeral=True)
        except Exception as error:
            await _send_error(interaction, error)

    @modifier_event.error
    @actualiser_events.error
    async def event_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        await _send_error(interaction, getattr(error, "original", error))
