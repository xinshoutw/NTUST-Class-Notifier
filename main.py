import asyncio
import logging
import os
import sys

import discord
import dotenv

from course_lookup import CourseClient, QueryPayload
from discord_bot import DiscordBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("httpx").propagate = False

# ========== Discord Bot 設定 ========== #
dotenv.load_dotenv()
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_TARGET_USER_IDS = list(map(int, os.environ.get("DISCORD_TARGET_USER_IDS").split(';')))
LOOK_UP_CLASSES = os.environ.get("LOOK_UP_CLASSES").split(';')


async def main():
    payloads = [
        QueryPayload(semester=class_.split('&')[0], course_no=class_.split('&')[1]) for class_ in LOOK_UP_CLASSES
    ]

    course_client = CourseClient(payloads=payloads)

    intents = discord.Intents.default()
    bot = None
    if DISCORD_BOT_TOKEN is not None:
        bot = DiscordBot(intents=intents, target_user_ids=DISCORD_TARGET_USER_IDS)
        asyncio.create_task(bot.start(DISCORD_BOT_TOKEN))

    while True:
        courses = await course_client.get_courses()
        for course_info, cur_member, limit in courses:
            if cur_member < limit and limit > 0:
                msg = (f"課程：{course_info}\n"
                       f"人數：{cur_member} / {limit}\n"
                       f"有空位囉，快去選課！\n"
                       f"https://courseselection.ntust.edu.tw/AddAndSub/B01/B01")
                logger.info(msg)

                if bot is not None:
                    await bot.send_dm(msg)
            else:
                logger.info(f"{course_info} 目前人數：{cur_member}/{limit} - 無空位")

        await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"程式已手動終止。")
        sys.exit()
