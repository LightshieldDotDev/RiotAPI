import threading
import logging
import asyncio
import pickle
import aio_pika
import traceback
import asyncpg


class SummonerProcessor(threading.Thread):

    def __init__(self, server, db):
        super().__init__()
        self.logging = logging.getLogger("SummonerProcessor")
        self.logging.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter('%(asctime)s [SummonerProcessor] %(message)s'))
        self.logging.addHandler(handler)

        self.stopped = False
        self.server = server
        self.sql = db
        self.db = None
        self.conn = None  # Connection object to the DB

    async def async_worker(self):
        channel = await self.connection.channel()
        await channel.set_qos(prefetch_count=100)
        queue = await channel.declare_queue(
            name=self.server + "_SUMMONER_TO_PROCESSOR",
            durable=True,
            robust=True
        )
        tasks = {}
        while not self.stopped:
            try:
                while len(tasks) < 100 and not self.stopped:
                    async with queue.iterator() as queue_iter:
                        async for message in queue_iter:
                            async with message.process():
                                elements = pickle.loads(message.body)
                                tasks[elements[0]] = elements
                            if len(tasks) >= 100 or self.stopped:
                                break

                    if len(tasks) < 100 and not self.stopped:
                        self.logging.info("Found no tasks. Taking a short break.")
                        await self.conn.close()  # close db connection when not finding messages
                        self.conn = None
                        await asyncio.sleep(5)

                self.logging.info("Inserting %s summoner.", len(tasks))
                value_lists = ["('%s', '%s', %s, %s, %s)" % tuple(task) for task in tasks.values()]
                values = ",".join(value_lists)
                query = """
                    INSERT INTO summoner 
                    (account_id, puuid, rank, wins, losses)
                    VALUES %s
                    ON CONFLICT (puuid)
                    DO
                        UPDATE SET rank = EXCLUDED.rank,
                                   wins = EXCLUDED.wins,
                                   losses = EXCLUDED.losses
                    ;
                    """ % values
                if not self.conn:
                    self.logging.info("Creating connection")
                    self.conn = await asyncpg.connect("postgresql://postgres@postgres/raw")
                await asyncio.sleep(2)
                self.logging.info("Executing query.")
                self.logging.info(await self.conn.execute(query))
                tasks = {}

            except Exception as err:
                traceback.print_tb(err.__traceback__)
                self.logging.info(err)
        await self.conn.close()

        await channel.close()

    async def run(self):
        self.logging.info("Initiated Worker.")
        self.connection = await aio_pika.connect_robust(
            "amqp://guest:guest@rabbitmq/", loop=asyncio.get_running_loop()
        )
        await asyncio.gather(*[asyncio.create_task(self.async_worker()) for _ in range(5)])

    def shutdown(self):
        self.stopped = True