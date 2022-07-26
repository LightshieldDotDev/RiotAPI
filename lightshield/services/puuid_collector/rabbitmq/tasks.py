"""Summoner ID Task Selector."""
import asyncio
import logging
import math
import aio_pika
import asyncpg

from lightshield.services.puuid_collector.rabbitmq import queries
from lightshield.rabbitmq_defaults import QueueHandler
from lightshield.config import Config


class Handler:
    platforms = {}
    is_shutdown = False
    db = None
    pika = None
    buffered_tasks = {}
    handlers = []

    def __init__(self):
        self.logging = logging.getLogger("Task Selector")
        self.config = Config()
        self.connector = self.config.get_db_connection()
        self.platforms = self.config.active_platforms
        for platform in self.platforms:
            self.buffered_tasks[platform] = {}

    async def init(self):
        self.db = await self.connector.init()
        self.pika = await aio_pika.connect_robust(
            self.config.rabbitmq._string, loop=asyncio.get_event_loop()
        )

    async def init_shutdown(self, *args, **kwargs):
        """Shutdown handler"""
        self.logging.info("Received shutdown signal.")
        self.is_shutdown = True
        for handler in self.handlers:
            handler.shutdown()

    async def handle_shutdown(self):
        """Close db connection pool after services have shut down."""
        await self.db.close()
        await self.pika.close()

    async def gather_tasks(self, platform, count):
        """Get tasks from db."""
        while not self.is_shutdown:
            async with self.db.acquire() as connection:
                query = queries.tasks[self.config.database].format(
                    platform=platform,
                    platform_lower=platform.lower(),
                    schema=self.config.db.schema,
                )
                try:
                    return await connection.fetch(query, count)

                except asyncpg.InternalServerError:
                    self.logging.info("Internal server error with db.")
            await asyncio.sleep(1)

    async def platform_handler(self, platform):
        self.logging.info("Worker %s up.", platform)
        # setup
        sections = 8
        section_size = 1000
        task_backlog = []
        handler = QueueHandler("puuid_tasks_%s" % platform)
        self.handlers.append(handler)
        await handler.init(durable=True, connection=self.pika)

        await handler.wait_threshold(0)

        while not self.is_shutdown:
            remaining_sections = math.ceil(
                (await handler.wait_threshold((sections - 1) * section_size)) / 1000
            )
            # Drop used up sections
            task_backlog = task_backlog[-remaining_sections * section_size :]
            # Get tasks
            while not self.is_shutdown:
                tasks = await self.gather_tasks(
                    platform=platform, count=sections * section_size
                )
                to_add = []
                for task in tasks:
                    if (sId := task["summoner_id"]) not in task_backlog:
                        task_backlog.append(sId)
                        to_add.append(sId.encode())
                    if len(task_backlog) >= sections * section_size:
                        break
                if not to_add:
                    await asyncio.sleep(10)
                    continue
                await handler.send_tasks(to_add, persistent=True)
                break

    async def run(self):
        """Run."""
        await self.init()
        await asyncio.gather(
            *[
                asyncio.create_task(self.platform_handler(platform))
                for platform in self.platforms
            ]
        )
        await self.handle_shutdown()
