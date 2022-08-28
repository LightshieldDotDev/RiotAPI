"""Summoner ID Task Selector."""
import asyncio
import logging

import aio_pika

import pickle

from lightshield.config import Config
from lightshield.services.match_history import queries
from lightshield.rabbitmq_defaults import QueueHandler


class Handler:
    platforms = {}
    is_shutdown = False
    db = None
    pika = None
    buffered_tasks = {}

    def __init__(self):
        self.logging = logging.getLogger("Task Selector")
        self.config = Config()
        self.connector = self.config.get_db_connection()
        self.platforms = self.config.active_platforms

    async def init(self):
        self.db = await self.connector.init()
        self.pika = await aio_pika.connect_robust(
            self.config.rabbitmq._string, loop=asyncio.get_event_loop()
        )

    async def init_shutdown(self, *args, **kwargs):
        """Shutdown handler"""
        self.logging.info("Received shutdown signal.")
        self.is_shutdown = True

    async def handle_shutdown(self):
        """Close db connection pool after services have shut down."""
        await self.db.close()
        await self.pika.close()

    async def process_results(self, message, platform, _type):
        """Put results from queue into list."""
        async with message.process(ignore_processed=True):
            self.buffered_tasks[platform][_type].append(message.body)
            await message.ack()

    async def insert_matches(self, platform):
        if not self.buffered_tasks[platform]["matches"]:
            return
        raw_tasks = self.buffered_tasks[platform]["matches"].copy()
        self.buffered_tasks[platform]["matches"] = []
        tasks_3 = {}
        tasks_2 = {}
        counter = 0
        for package in [pickle.loads(task) for task in raw_tasks]:
            for match in package:
                try:
                    match_platform = match[0]
                except Exception as err:
                    self.logging.error(err)
                    self.logging.info(match)
                    raise err

                if len(match) == 3:
                    if match_platform not in tasks_3:
                        tasks_3[match_platform] = []
                    tasks_3[match_platform].append(match)
                else:
                    if match_platform not in tasks_2:
                        tasks_2[match_platform] = []
                    tasks_2[match_platform].append(match)
                counter += 1

        self.logging.debug(" %s\t | %s matches inserted", platform, counter)
        async with self.db.acquire() as connection:

            if tasks_3:
                for match_platform, tasks in tasks_3.items():
                    prep = await connection.prepare(
                        queries.insert_queue_known.format(
                            platform_lower=match_platform.lower()
                        )
                    )
                    await prep.executemany(tasks)
            if tasks_2:
                for match_platform, tasks in tasks_2.items():
                    prep = await connection.prepare(
                        queries.insert_queue_known.format(
                            platform_lower=match_platform.lower()
                        )
                    )
                    await prep.executemany(tasks)

    async def platform_thread(self, platform):
        try:
            matches_queue = QueueHandler("match_history_results_%s" % platform)
            await matches_queue.init(durable=True, connection=self.pika)

            self.buffered_tasks[platform] = {"matches": []}

            cancel_consume_matches = await matches_queue.consume_tasks(
                self.process_results, {"platform": platform, "_type": "matches"}
            )

            while not self.is_shutdown:

                await self.insert_matches(platform)

                for _ in range(30):
                    await asyncio.sleep(1)
                    if self.is_shutdown:
                        break

            await cancel_consume_matches()
            await asyncio.sleep(2)
            await self.insert_matches(platform)
        except Exception as err:
            self.logging.info(err)

    async def run(self):
        """Run."""
        await self.init()

        await asyncio.gather(
            *[
                asyncio.create_task(self.platform_thread(platform=platform))
                for platform in self.platforms
            ]
        )

        await self.handle_shutdown()