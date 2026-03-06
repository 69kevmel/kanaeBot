import discord
from discord import app_commands
from discord.ext import commands
import random
import logging


logger = logging.getLogger(__name__)

# --- INFOS SUR LES RÔLES (Pour styliser les Messages Privés) ---
ROLE_INFOS = {
    "loup": {"name": "Loup-Garou", "emoji": "🐺", "desc": "Tu es un Loup-Garou ! Chaque nuit, concerte-toi avec tes alliés dans le salon secret pour dévorer un villageois. Sois discret le jour."},
    "loup-garou": {"name": "Loup-Garou", "emoji": "🐺", "desc": "Tu es un Loup-Garou ! Chaque nuit, concerte-toi avec tes alliés dans le salon secret pour dévorer un villageois."},
    "voyante": {"name": "Voyante", "emoji": "🔮", "desc": "Tu es la Voyante ! Chaque nuit, envoie un MP au Maître du Jeu pour découvrir le rôle secret d'un joueur."},
    "sorciere": {"name": "Sorcière", "emoji": "🧪", "desc": "Tu es la Sorcière ! Tu as 2 potions (Vie et Mort). Le MDJ t'appellera la nuit, réponds-lui en MP Discord ! (Ne te démute pas)."},
    "sorcière": {"name": "Sorcière", "emoji": "🧪", "desc": "Tu es la Sorcière ! Tu as 2 potions (Vie et Mort). Le MDJ t'appellera la nuit, réponds-lui en MP Discord ! (Ne te démute pas)."},
    "chasseur": {"name": "Chasseur", "emoji": "🔫", "desc": "Tu es le Chasseur ! Si tu meurs, tu DOIS emporter un autre joueur avec toi dans la tombe avant de rendre l'âme."},
    "cupidon": {"name": "Cupidon", "emoji": "🏹", "desc": "Tu es Cupidon ! La première nuit, envoie un MP au MDJ avec le nom de 2 joueurs pour les lier par l'amour éternel."},
    "petite fille": {"name": "Petite Fille", "emoji": "👧", "desc": "Tu es la Petite Fille ! Tu as le droit d'espionner secrètement ce que fait le MDJ la nuit (si vous avez vos propres règles)."},
    "voleur": {"name": "Voleur", "emoji": "🥷", "desc": "Tu es le Voleur ! La première nuit, tu pourras choisir d'échanger ton rôle avec l'une des 2 cartes non distribuées."},
    "villageois": {"name": "Simple Villageois", "emoji": "🌾", "desc": "Tu es un Simple Villageois. Aucun pouvoir magique, juste ton cerveau pour débusquer les loups et survivre !"}
}

class LGGameState:
    """Classe pour stocker l'état de la partie pour un serveur donné"""
    def __init__(self, gm: discord.Member):
        self.gm = gm
        self.is_active = True
        self.lobby_open = True
        self.players = {}  # {user_id: discord.Member}
        self.roles = {}    # {user_id: role_str}
        self.alive = []    # Liste des user_id encore en vie
        self.dead = []     # Liste de dict [{"member": discord.Member, "role": str}]
        self.wolves_channel = None  # Le channel privé #tanière-des-loups

class LGStartView(discord.ui.View):
    def __init__(self, game: LGGameState):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Rejoindre le village 🐺", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.game.lobby_open:
            await interaction.response.send_message("❌ Trop tard, les inscriptions sont fermées !", ephemeral=True)
            return
            
        if interaction.user.id in self.game.players:
            await interaction.response.send_message("✅ Tu es déjà inscrit dans le village !", ephemeral=True)
            return
            
        self.game.players[interaction.user.id] = interaction.user
        self.game.alive.append(interaction.user.id)
        
        # Mise à jour de l'affichage
        embed = interaction.message.embeds[0]
        mentions = " ".join([p.mention for p in self.game.players.values()])
        embed.description = f"**Joueurs inscrits ({len(self.game.players)}) :**\n{mentions}\n\n*Cliquez sur le bouton vert pour participer !*"
        
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="🔒 Fermer & Lancer", style=discord.ButtonStyle.danger)
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.gm.id:
            await interaction.response.send_message("❌ Seul le Maître du Jeu peut fermer les inscriptions.", ephemeral=True)
            return
            
        self.game.lobby_open = False
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🔒 **Les inscriptions sont fermées !**\nLe Maître du Jeu peut maintenant distribuer les rôles avec `/lg-roles`.")

class LoupGarouCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games = {}  # {guild_id: LGGameState}

    # ---------------------------------------
    # /lg-start
    # ---------------------------------------
    @app_commands.command(name="lg-start", description="(MDJ) Ouvre le lobby pour une partie de Loup-Garou !")
    @app_commands.default_permissions(manage_messages=True) # Réservé au staff / animateurs
    async def lg_start(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        
        if guild_id in self.games:
            await interaction.response.send_message("❌ Une partie est déjà en cours ! Utilisez `/lg-fin` d'abord.", ephemeral=True)
            return

        # Initialisation de la partie
        game = LGGameState(gm=interaction.user)
        self.games[guild_id] = game

        embed = discord.Embed(
            title="🌕 UNE PARTIE DE LOUP-GAROU VA COMMENCER 🌕",
            description="**Joueurs inscrits (0) :**\n\n*Cliquez sur le bouton vert pour participer !*",
            color=discord.Color.dark_purple()
        )
        embed.set_footer(text=f"Maître du Jeu : {interaction.user.display_name}")

        view = LGStartView(game)
        await interaction.response.send_message(embed=embed, view=view)

    # ---------------------------------------
    # /lg-roles
    # ---------------------------------------
    @app_commands.command(name="lg-roles", description="(MDJ) Distribue les rôles en secret aux joueurs inscrits")
    @app_commands.describe(roles_liste="Sépare les rôles par une virgule (ex: Loup, Loup, Voyante, Chasseur, Villageois)")
    async def lg_roles(self, interaction: discord.Interaction, roles_liste: str):
        game = self.games.get(interaction.guild_id)
        
        if not game or game.gm.id != interaction.user.id:
            await interaction.response.send_message("❌ Vous n'êtes pas le MDJ d'une partie active.", ephemeral=True)
            return
            
        if game.lobby_open:
            await interaction.response.send_message("❌ Ferme d'abord le lobby avec le bouton rouge 🔒 avant de distribuer les rôles !", ephemeral=True)
            return

        if len(game.roles) > 0:
            await interaction.response.send_message("❌ Les rôles ont DÉJÀ été distribués !", ephemeral=True)
            return

        # Nettoyage de la liste des rôles
        roles = [r.strip() for r in roles_liste.split(",")]
        players_list = list(game.players.values())
        
        if len(roles) != len(players_list):
            await interaction.response.send_message(f"❌ Erreur de compte : Tu as donné **{len(roles)}** rôles, mais il y a **{len(players_list)}** joueurs inscrits.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False) # On defer car ça peut prendre un peu de temps

        # Distribution aléatoire
        random.shuffle(roles)
        wolves_members = []
        
        for player, role in zip(players_list, roles):
            game.roles[player.id] = role
            
            # Préparation du MP
            role_key = role.lower()
            info = ROLE_INFOS.get(role_key, {"name": role.capitalize(), "emoji": "🃏", "desc": "Garde ce rôle secret et écoute le MDJ !"})
            
            embed_dm = discord.Embed(
                title=f"{info['emoji']} TON RÔLE : {info['name']} {info['emoji']}",
                description=f"**{info['desc']}**\n\n*🤫 Ne révèle ce message à personne ! La partie va bientôt commencer dans le vocal.*",
                color=discord.Color.dark_red() if "loup" in role_key else discord.Color.blue()
            )
            
            try:
                await player.send(embed=embed_dm)
            except discord.Forbidden:
                await interaction.channel.send(f"⚠️ Impossible d'envoyer un MP à {player.mention}. Ses MPs sont sûrement fermés !")

            if "loup" in role_key:
                wolves_members.append(player)

        # Création du salon secret des loups
        guild = interaction.guild
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            game.gm: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        for wolf in wolves_members:
            overwrites[wolf] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            category = interaction.channel.category
            wolves_channel = await guild.create_text_channel("tanière-des-loups-🤫", overwrites=overwrites, category=category)
            game.wolves_channel = wolves_channel
            
            mentions_loups = " ".join([w.mention for w in wolves_members])
            await wolves_channel.send(f"🩸 **BIENVENUE DANS LA TANIÈRE** 🩸\n\n{mentions_loups} Vous êtes les Loups-Garous ! La nuit, utilisez ce salon secret pour vous mettre d'accord sur qui dévorer.\n\n*(Le Maître du Jeu {game.gm.mention} voit également ce salon)*.")
            
        except discord.Forbidden:
            await interaction.channel.send("⚠️ Je n'ai pas la permission de créer le salon secret des loups !")

        await interaction.followup.send("✅ **LES RÔLES ONT ÉTÉ DISTRIBUÉS EN SECRET !** 🃏\n*(Regardez vos Messages Privés !)*")

    # ---------------------------------------
    # /lg-nuit
    # ---------------------------------------
    @app_commands.command(name="lg-nuit", description="(MDJ) Annonce que la nuit tombe")
    async def lg_nuit(self, interaction: discord.Interaction):
        game = self.games.get(interaction.guild_id)
        if not game or game.gm.id != interaction.user.id:
            await interaction.response.send_message("❌ Tu n'es pas le MDJ !", ephemeral=True)
            return
            
        embed = discord.Embed(
            title="🌙 LA NUIT TOMBE SUR KANAÉ...",
            description="**Tout le monde ferme les yeux et se mute en vocal !** 🤫\n\n*(Les loups se réveillent dans leur salon secret, les autres personnages avec pouvoir attendent l'appel du Maître du Jeu).* \n\n💤 *Chuuut...*",
            color=discord.Color.dark_blue()
        )
        embed.set_image(url="https://i.imgur.com/uR1a34G.gif") # Un petit gif d'ambiance loup-garou
        await interaction.response.send_message(embed=embed)

    # ---------------------------------------
    # /lg-jour
    # ---------------------------------------
    @app_commands.command(name="lg-jour", description="(MDJ) Annonce le lever du jour et affiche l'état du village")
    async def lg_jour(self, interaction: discord.Interaction):
        game = self.games.get(interaction.guild_id)
        if not game or game.gm.id != interaction.user.id:
            await interaction.response.send_message("❌ Tu n'es pas le MDJ !", ephemeral=True)
            return

        embed = discord.Embed(
            title="☀️ LE SOLEIL SE LÈVE !",
            description="**Le village se réveille...** Tout le monde peut ouvrir les yeux et rallumer son micro ! 🐓\nMais y a-t-il eu des morts cette nuit ?",
            color=discord.Color.gold()
        )
        
        # Le Dashboard des Vivants et Morts
        vivants = "\n".join([f"🟢 <@{pid}>" for pid in game.alive]) if game.alive else "Plus personne..."
        morts = "\n".join([f"💀 ~~{m['member'].display_name}~~ *(Était : {m['role']})*" for m in game.dead]) if game.dead else "Personne n'est mort (pour l'instant)."

        embed.add_field(name="🙋‍♂️ En Vie", value=vivants, inline=True)
        embed.add_field(name="🪦 Cimetière", value=morts, inline=True)

        await interaction.response.send_message(embed=embed)

    # ---------------------------------------
    # /lg-kill
    # ---------------------------------------
    @app_commands.command(name="lg-kill", description="(MDJ) Tue un joueur, révèle son rôle, et le MUTE en vocal")
    @app_commands.describe(joueur="Le joueur qui vient de mourir")
    async def lg_kill(self, interaction: discord.Interaction, joueur: discord.Member):
        game = self.games.get(interaction.guild_id)
        if not game or game.gm.id != interaction.user.id:
            await interaction.response.send_message("❌ Tu n'es pas le MDJ !", ephemeral=True)
            return

        if joueur.id not in game.alive:
            await interaction.response.send_message(f"❌ {joueur.display_name} n'est pas dans la liste des vivants !", ephemeral=True)
            return

        await interaction.response.defer()

        # Récupération du rôle
        role = game.roles.get(joueur.id, "Inconnu")
        
        # Mise à jour des listes
        game.alive.remove(joueur.id)
        game.dead.append({"member": joueur, "role": role})

        # MUTE SERVEUR
        mute_status = "*(Mute Serveur échoué : il n'est peut-être pas en vocal ?)*"
        try:
            await joueur.edit(mute=True, reason="Mort au Loup-Garou")
            mute_status = "*(Mute Serveur appliqué 🔇)*"
        except discord.HTTPException:
            pass # Il n'est pas en vocal ou manque de permissions
        except discord.Forbidden:
            mute_status = "*(Erreur : Je n'ai pas la permission de le mute)*"

        embed = discord.Embed(
            title="🩸 UN MORT DANS LE VILLAGE !",
            description=f"{joueur.mention} nous a quitté...\n\nIl était : **{role}** 🃏\n\n{mute_status}\n\n*Les morts ne parlent pas. Bonne chance aux survivants.*",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)

    # ---------------------------------------
    # /lg-fin
    # ---------------------------------------
    @app_commands.command(name="lg-fin", description="(MDJ) Termine la partie, supprime les salons secrets et démute tout le monde")
    async def lg_fin(self, interaction: discord.Interaction):
        game = self.games.get(interaction.guild_id)
        if not game:
            await interaction.response.send_message("❌ Aucune partie en cours.", ephemeral=True)
            return
            
        if game.gm.id != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Seul le MDJ ou un Admin peut forcer la fin de partie.", ephemeral=True)
            return

        await interaction.response.defer()

        # Suppression du salon des loups
        if game.wolves_channel:
            try:
                await game.wolves_channel.delete(reason="Fin de la partie LG")
            except Exception as e:
                logger.error(f"Impossible de supprimer le salon des loups : {e}")

        # UNMUTE de tous les morts
        unmuted_count = 0
        for dead_player in game.dead:
            try:
                member = dead_player["member"]
                await member.edit(mute=False, reason="Fin de la partie LG")
                unmuted_count += 1
            except Exception:
                pass # S'il a leave le vocal ou le serv, on ignore

        # Nettoyage
        del self.games[interaction.guild_id]

        embed = discord.Embed(
            title="🛑 LA PARTIE EST TERMINÉE !",
            description=f"Le Maître du Jeu a clôturé la partie.\n\n🧹 Le salon des loups a été détruit.\n🎙️ **{unmuted_count}** morts ont retrouvé la parole (démutés).\n\n*Prêts pour une revanche ? 🐺*",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    # On ajoute la Cog au bot globalement
    await bot.add_cog(LoupGarouCog(bot))

    logger.info("🐺 Module Loup-Garou chargé avec succès !")
