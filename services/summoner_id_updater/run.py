import asyncio
import os
import websockets
import logging
import json
import aiohttp
from datetime import datetime, timedelta

if "SERVER" not in os.environ:
    print("No server provided, shutting down")
    exit()
server = os.environ['SERVER']

from redis_connector import Redis
from pika_connector import Pika

class Worker:

    def __init__(self, buffer):

        self.redis = Redis()
        self.pika = Pika()
        self.url_template = f"http://{server}.api.riotgames.com/lol/summoner/v4/summoners/%s"

        self.max_buffer = buffer
        self.failed_tasks = []
        self.retry_after = datetime.now()
        self.buffered_summoners = {}

        self.logging = logging.getLogger("worker")
        self.logging.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(
            logging.Formatter(f'%(asctime)s [WORKER] %(message)s'))
        self.logging.addHandler(ch)


    async def next_task(self):
        while True:
            msg = await self.pika.get()
            if not msg:
                self.logging.info("No messages found. Awaiting.")
                while not msg:
                    msg = await self.pika.get()
                    await asyncio.sleep(1)

            content = json.loads(msg.body.decode('utf-8'))
            summonerId = content['summonerId']
            if summonerId in self.buffered_summoners:  # Skip any further tasks for already queued
                self.logging.info(f"Summoner id {summonerId} is already registered as an active task.")
                try:
                    await msg.ack()
                except:
                    self.logging.info(f"Failed to ack {summonerId}.")
                continue
            redis_entry = await self.redis.hgetall(summonerId)

            if redis_entry:  # Skip call for already existing. Still adds a message output
                package = {**content, **redis_entry}
                await self.pika.push(package)
                await msg.ack()
                continue
            self.buffered_summoners[summonerId] = True
            return summonerId, msg

    async def worker(self):
        while True:
            if self.retry_after > datetime.now():
                delay = (datetime.now() - self.retry_after).total_seconds()
                await asyncio.sleep(delay)
            summonerId, msg = await self.next_task()
            url = self.url_template % (summonerId)
            self.logging.debug(f"Fetching {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy="http://proxy:8000") as response:
                    try:
                        resp = await response.json(content_type=None)
                    except:
                        pass
            if response.status in [429, 430]:
                if "Retry-After" in response.headers:
                    delay = int(response.headers['Retry-After'])
                    self.retry_after = datetime.now() + timedelta(seconds=delay)
            elif response.status == 404:
                msg.reject(requeue=False)
            if response.status != 200:
                msg.reject(requeue=True)
            else:
                await self.redis.hset(
                    summonerId=summonerId,
                    mapping={'puuid': resp['puuid'],
                             'accountId': resp['accountId']})
                await msg.ack()
                package = {**json.loads(msg.body.decode('utf-8')),
                           **resp}
                await self.pika.push(package)
            del self.buffered_summoners[summonerId]


    async def main(self):
        await asyncio.gather(*[self.worker() for i in range(self.max_buffer)])

    async def run(self):
        await self.pika.init()
        await self.main()

if __name__ == "__main__":

    worker = Worker(buffer=30)
    asyncio.run(worker.run())
