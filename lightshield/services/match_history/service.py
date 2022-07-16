import asyncio
import logging
import pickle
from datetime import datetime, timedelta

import aiohttp

from lightshield.rabbitmq_defaults import QueueHandler


class Platform:
    running = False
    _runner = None
    matches_queue = summoner_queue = None

    def __init__(self, region, platform, config, handler):
        self.region = region
        self.platform = platform
        self.handler = handler
        self.logging = logging.getLogger("%s" % platform)
        self.service = config.services.match_history

        self.proxy = handler.proxy
        self.endpoint_url = (
            f"{config.connections.proxy.protocol}://{self.region.lower()}.api.riotgames.com"
            f"/lol/match/v5/matches/by-puuid/%s/ids"
            f"?count=100"
        )
        if self.service.type:
            self.endpoint_url += "&type=%s" % self.service.type
        if self.service.queue:
            self.endpoint_url += "&queue=%s" % self.service.queue

    async def process_tasks(self, message):
        async with message.process(ignore_processed=True):
            puuid, latest_match, latest_history_update = pickle.loads(message.body)
            now = datetime.now() - timedelta(days=self.service.history.days)
            now_tst = int(now.timestamp())
            url = self.endpoint_url % puuid
            url += "&startTime=%s" % now_tst
            start_index = 0
            is_404 = False
            newest_match = None
            matches = []
            found_latest = False
            while (
                start_index < self.service.history.matches
                and not is_404
                and not self.handler.is_shutdown
                and not found_latest
            ):
                task_url = url + "&start=%s" % start_index
                try:
                    async with self.session.get(task_url, proxy=self.proxy) as response:
                        match response.status:
                            case 200:
                                matches_found = await response.json()
                                if not matches_found:
                                    break
                                if start_index == 0:
                                    newest_match = int(matches_found[0].split("_")[1])
                                start_index += 100
                                for match in matches_found:
                                    platform, id = match.split("_")
                                    if id == latest_match:
                                        found_latest = True
                                        break
                                    if self.service.queue:
                                        matches.append(
                                            (platform, int(id), self.service.queue)
                                        )
                                    else:
                                        matches.append((platform, int(id)))
                            case 404:
                                is_404 = True
                            case 429:
                                await asyncio.sleep(0.5)
                            case 430:
                                data = await response.json()
                                wait_until = datetime.fromtimestamp(data["Retry-At"])
                                seconds = (wait_until - datetime.now()).total_seconds()
                                seconds = max(0.1, seconds)
                                await asyncio.sleep(seconds)
                            case _:
                                await asyncio.sleep(0.1)
                except aiohttp.ClientProxyConnectionError:
                    await asyncio.sleep(0.1)
                    continue
            if not newest_match:
                newest_match = found_latest
            matches = list(set(matches))
            await self.matches_queue.send_tasks(
                [pickle.dumps(match) for match in matches], persistent=True
            )
            await self.summoner_queue.send_tasks(
                [pickle.dumps((puuid, newest_match, now))]
            )
            self.logging.info("Updated user %s, found %s matches", puuid, len(matches))
            await message.ack()

    async def run(self):
        task_queue = QueueHandler("match_history_tasks_%s" % self.platform)
        await task_queue.init(
            durable=True, prefetch_count=20, connection=self.handler.pika
        )

        self.matches_queue = QueueHandler(
            "match_history_results_matches_%s" % self.platform
        )
        await self.matches_queue.init(durable=True, connection=self.handler.pika)

        self.summoner_queue = QueueHandler(
            "match_history_results_summoners_%s" % self.platform
        )
        await self.summoner_queue.init(durable=True, connection=self.handler.pika)

        cancel_consume = await task_queue.consume_tasks(self.process_tasks)
        conn = aiohttp.TCPConnector(limit=0)
        self.session = aiohttp.ClientSession(connector=conn)

        while not self.handler.is_shutdown:
            await asyncio.sleep(1)

        await cancel_consume()
        await asyncio.sleep(10)
