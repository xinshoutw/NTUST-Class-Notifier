# discord_bot.py
import asyncio
import logging

import discord

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DiscordBot(discord.Client):
    """
    這是一個簡易的 Discord Bot 範例。
    - 不包含 CourseClient 相關邏輯，只專注在機器人的連線與傳送訊息。
    """

    def __init__(self, *, intents: discord.Intents, target_user_ids: list[int]):
        super().__init__(intents=intents)
        self.target_user_ids = tuple(target_user_ids)
        self.ready_event = asyncio.Event()

    async def on_ready(self):
        logger.info(f"已登入 Discord：{self.user}")
        self.ready_event.set()
        await self.send_dm("服務已啟動，此為測試訊息")

    async def send_dm(self, message: str):
        """
        傳送私訊給預設的 target_user_id。
        """
        await self.ready_event.wait()  # 確保 Bot 已經 on_ready

        for target_user_id in self.target_user_ids:
            try:
                user = await self.fetch_user(target_user_id)
                await user.send(message)
                logger.debug(f"已向 <@{target_user_id}> 發送訊息：{message}")
            except discord.DiscordException as e:
                logger.error(f"傳送 Discord 私訊發生錯誤：{e}")
