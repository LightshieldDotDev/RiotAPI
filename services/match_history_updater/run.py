"""Match History updater. Pulls matchlists for all player."""

import asyncio
import json
import os
from datetime import datetime

from worker import (
    Worker,
    RatelimitException,
    NotFoundException,
    Non200Exception,
    NoMessageException
)

if "SERVER" not in os.environ:
    print("No SERVER env variable provided. exiting")
    exit()

server = os.environ['SERVER']


class MatchHistoryUpdater(Worker):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required_matches = int(os.environ['MATCHES_TO_UPDATE'])
        try:
            self.timelimit = int(os.environ['TIME_LIMIT'])
        except:
            self.timelimit = None

    async def initiate_pika(self, connection):

        channel = await connection.channel()
        await channel.set_qos(prefetch_count=50)
        # Incoming
        incoming = await channel.declare_queue(
            'MATCH_HISTORY_IN_' + self.server, durable=True)
        # Outgoing
        outgoing = await channel.declare_exchange(
            f'MATCH_HISTORY_OUT_{self.server}', type='direct',
            durable=True)

        # Output to the Match_Updater
        match_in = await channel.declare_queue(
            f'MATCH_IN_{self.server}',
            durable=True
        )
        await match_in.bind(outgoing, 'MATCH')

        await self.pika.init(incoming=incoming, outgoing=outgoing, tag='MATCH', no_ack=True)

    async def is_valid(self, identifier, content, msg):
        """Return true if the msg should be passed on.

        If not valid this method properly handles the msg.
        """
        if identifier in self.buffered_elements:
            return False
        matches = content['wins'] + content['losses']
        if prev := await self.redis.hgetall(f"user:{identifier}"):
            matches -= (int(prev['wins']) + int(prev['losses']))
        if matches < self.required_matches:  # Skip if less than required new matches
            # TODO: Despite not having enough matches this should be considered to pass on to the db
            return False
        return {"matches": matches}

    async def handler(self, session, url):
        rate_flag = False
        while True:
            if datetime.now() < self.retry_after or rate_flag:
                rate_flag = False
                delay = max(0.5, (self.retry_after - datetime.now()).total_seconds())
                await asyncio.sleep(delay)
            try:
                response = await self.fetch(session, url)
                if not self.timelimit:
                    return [match['gameId'] for match in response['matches'] if
                            match['queue'] == 420 and
                            match['platformId'] == server]
                return [match['gameId'] for match in response['matches'] if
                        match['queue'] == 420 and
                        match['platformId'] == server and
                        int(str(match['timestamp'])[:10]) >= self.timelimit]

            except RatelimitException:
                rate_flag = True
            except Non200Exception:
                pass

    async def process(self, session, identifier, msg, matches):
        """Manage a single summoners full history calls."""
        matches_to_call = matches + 3
        calls = int(matches_to_call / 100) + 1
        ids = [start_id * 100 for start_id in range(calls)]
        content = json.loads(msg.body.decode('utf-8'))
        calls_executed = []
        while ids:
            id = ids.pop()
            calls_executed.append(asyncio.create_task(
                self.handler(
                    session=session,
                    url=self.url_template % (content['accountId'], id, id + 100))
            ))
            await asyncio.sleep(0.01)
        try:
            responses = await asyncio.gather(*calls_executed)
            matches = list(set().union(*responses))
            return [0, identifier, content, matches]
        except NotFoundException:  # Triggers only if a call returns 404. Forces a full reject.
            return [1]
        finally:
            del self.buffered_elements[identifier]

    async def finalize(self, responses):

        for identifier, content, matches in [entry[1:] for entry in responses if entry[0] == 0]:

            await self.redis.hset(
                key=f"user:{identifier}",
                mapping={
                    "summonerName": content['summonerName'],
                    "wins": content['wins'],
                    "losses": content['losses'],
                    "tier": content['tier'],
                    "rank": content['rank'],
                    "leaguePoints": content['leaguePoints']
                }
            )
            while matches:
                id = matches.pop()
                await self.pika.push(id)


if __name__ == "__main__":
    buffer = int(os.environ['BUFFER'])
    worker = MatchHistoryUpdater(
        buffer=buffer,
        url=f"http://{os.environ['SERVER']}.api.riotgames.com/lol/match/v4/matchlists/by-account/" \
           "%s?beginIndex=%s&endIndex=%s&queue=420",
        identifier="summonerId",
        chunksize=100,
        message_out=f"MATCH_IN_{os.environ['SERVER']}")
    asyncio.run(worker.main())
