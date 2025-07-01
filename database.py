import logging
import aiomysql
from datetime import date

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

