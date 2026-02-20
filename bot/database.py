import logging
import aiomysql
from datetime import date, datetime, timezone, timedelta

from . import config

logger = logging.getLogger(__name__)

db_pool = None

async def init_db_pool():
    try:
        pool = await aiomysql.create_pool(
            host=config.MYSQLHOST,
            port=config.MYSQLPORT,
            user=config.MYSQLUSER,
            password=config.MYSQLPASSWORD,
            db=config.MYSQLDATABASE,
            autocommit=True,
        )
        logger.info("DB pool created: %s@%s:%s/%s", config.MYSQLUSER, config.MYSQLHOST, config.MYSQLPORT, config.MYSQLDATABASE)
        return pool
    except Exception as e:
        logger.exception("Unable to create MySQL pool: %s", e)
        raise

async def ensure_tables(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS booster_cooldowns (
                    user_id BIGINT PRIMARY KEY,
                    last_opened DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )   
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scores (
                    user_id BIGINT PRIMARY KEY,
                    points INT NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_limits (
                    user_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    date DATE NOT NULL,
                    PRIMARY KEY(user_id, channel_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reaction_tracker (
                    message_id BIGINT NOT NULL,
                    reactor_id BIGINT NOT NULL,
                    PRIMARY KEY(message_id, reactor_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS news_history (
                    link VARCHAR(768) PRIMARY KEY,
                    date DATE NOT NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS recap_history (
                    sent_date DATE PRIMARY KEY
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """CREATE TABLE IF NOT EXISTS thread_participation (
                    thread_id BIGINT,
                    user_id BIGINT,
                    PRIMARY KEY(thread_id, user_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

                CREATE TABLE IF NOT EXISTS thread_daily_creations (
                    user_id BIGINT,
                    date DATE,
                    PRIMARY KEY(user_id, date)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
                )
            # Twitch links table
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_links (
                    user_id BIGINT,
                    platform VARCHAR(50),
                    username VARCHAR(255),
                    PRIMARY KEY(user_id, platform),
                    UNIQUE(platform, username)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_rewards (
                    user_id BIGINT,
                    platform VARCHAR(50),
                    PRIMARY KEY(user_id, platform)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS twitch_sub_claims (
                    user_id BIGINT PRIMARY KEY,
                    last_claimed DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_account_rewards (
                    platform VARCHAR(50),
                    username VARCHAR(255),
                    PRIMARY KEY(platform, username)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
                """
            )

            # Pokeweed tables
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS pokeweeds (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100),
                    hp INT,
                    capture_points INT,
                    power INT,
                    rarity VARCHAR(50),
                    drop_rate FLOAT
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_pokeweeds (
                    user_id BIGINT,
                    pokeweed_id INT,
                    capture_date DATETIME,
                    PRIMARY KEY (user_id, pokeweed_id, capture_date),
                    FOREIGN KEY (pokeweed_id) REFERENCES pokeweeds(id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            """)
    logger.info("Database tables checked/created")

async def get_user_points(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0] if row else 0

async def set_user_points(pool, user_id, pts):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scores (user_id, points) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE points = %s;
                """,
                (int(user_id), pts, pts),
            )
            return pts

async def add_points(pool, user_id, pts):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO scores (user_id, points) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE points = points + %s;
                """,
                (int(user_id), pts, pts),
            )
            await cur.execute("SELECT points FROM scores WHERE user_id=%s;", (int(user_id),))
            row = await cur.fetchone()
            return row[0]

async def has_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT 1 FROM daily_limits
                WHERE user_id=%s AND channel_id=%s AND date=%s;
                """,
                (int(user_id), int(channel_id), date),
            )
            return await cur.fetchone() is not None

async def set_daily_limit(pool, user_id, channel_id, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO daily_limits (user_id, channel_id, date)
                VALUES (%s, %s, %s);
                """,
                (int(user_id), int(channel_id), date),
            )

async def has_reaction_been_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT 1 FROM reaction_tracker
                WHERE message_id=%s AND reactor_id=%s;
                """,
                (int(message_id), int(reactor_id)),
            )
            return await cur.fetchone() is not None

async def set_reaction_counted(pool, message_id, reactor_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO reaction_tracker (message_id, reactor_id)
                VALUES (%s, %s);
                """,
                (int(message_id), int(reactor_id)),
            )

async def get_top_n(pool, n=10):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT user_id, points FROM scores
                ORDER BY points DESC
                LIMIT %s;
                """,
                (n,),
            )
            return await cur.fetchall()

async def has_sent_news(pool, link):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM news_history WHERE link=%s;", (link,))
            return await cur.fetchone() is not None

async def mark_news_sent(pool, link, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO news_history (link, date)
                VALUES (%s, %s);
                """,
                (link, date),
            )

async def has_sent_recap(pool, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM recap_history WHERE sent_date=%s;",
                (date,),
            )
            return await cur.fetchone() is not None

async def mark_recap_sent(pool, date):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT IGNORE INTO recap_history (sent_date) VALUES (%s);",
                (date,),
            )

async def get_last_recap_date(pool):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT MAX(sent_date) FROM recap_history;")
            row = await cur.fetchone()
            return row[0] if row and row[0] else None

# Twitch account linking functions
async def get_social_by_discord(pool, user_id, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT username FROM social_links WHERE user_id = %s AND platform = %s;",
                (int(user_id), platform)
            )
            row = await cur.fetchone()
            return row[0] if row else None

async def get_discord_by_social(pool, username, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM social_links WHERE username = %s AND platform = %s;",
                (username, platform)
            )
            row = await cur.fetchone()
            return row[0] if row else None

async def link_social_account(pool, user_id, platform, username):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. Vérifie si le pseudo est pris par un AUTRE joueur sur cette plateforme
            await cur.execute(
                "SELECT user_id FROM social_links WHERE platform = %s AND username = %s;",
                (platform, username)
            )
            row = await cur.fetchone()
            if row and row[0] != int(user_id):
                return False

            # 2. Ajoute ou met à jour le lien
            await cur.execute(
                """
                INSERT INTO social_links (user_id, platform, username) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE username = %s;
                """,
                (int(user_id), platform, username, username),
            )
            return True

async def unlink_social_account(pool, user_id, platform):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM social_links WHERE user_id = %s AND platform = %s;",
                (int(user_id), platform)
            )

async def check_and_reward_social_link(pool, user_id, platform, username):
    # Double sécurité : On vérifie le compte Discord ET le pseudo Twitch
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # 1. Est-ce que ce compte Discord a déjà eu les points pour ce réseau ?
            await cur.execute("SELECT 1 FROM social_rewards WHERE user_id = %s AND platform = %s;", (int(user_id), platform))
            discord_used = await cur.fetchone()
            
            # 2. Est-ce que ce pseudo Twitch a déjà été utilisé par quelqu'un pour gratter les points ?
            await cur.execute("SELECT 1 FROM social_account_rewards WHERE platform = %s AND username = %s;", (platform, username))
            social_used = await cur.fetchone()
            
            # Si ni le Discord ni le Twitch n'ont été utilisés, on donne les points !
            if not discord_used and not social_used:
                # On verrouille le Discord
                await cur.execute("INSERT INTO social_rewards (user_id, platform) VALUES (%s, %s);", (int(user_id), platform))
                # On verrouille le compte Twitch !
                await cur.execute("INSERT INTO social_account_rewards (platform, username) VALUES (%s, %s);", (platform, username))
                return True
                
            return False
        
async def claim_twitch_sub_reward(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT last_claimed FROM twitch_sub_claims WHERE user_id = %s;", (int(user_id),))
            row = await cur.fetchone()
            
            now = datetime.now(timezone.utc)
            
            if row and row[0]:
                last_claimed = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                # On vérifie si 28 jours se sont écoulés depuis le dernier claim de sub
                if now - last_claimed < timedelta(days=28):
                    return False # Trop tôt, il a déjà pris ses points pour ce mois-ci
            
            # S'il n'a jamais claim, ou si ça fait + de 28 jours : on met à jour la date et on l'autorise
            await cur.execute(
                """
                INSERT INTO twitch_sub_claims (user_id, last_claimed) 
                VALUES (%s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE last_claimed = UTC_TIMESTAMP();
                """,
                (int(user_id),)
            )
            return True
        
async def get_all_socials_by_discord(pool, user_id):
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT platform, username FROM social_links WHERE user_id = %s;",
                (int(user_id),)
            )
            return await cur.fetchall()