import asyncio
from aiohttp.web import middleware
import os
import threading
import time
from datetime import datetime

class Headers:
    """Middleware that adds the Riot API Key to the request."""
    def __init__(self):
        if not "API_KEY" in os.environ:
            raise Exception("No API Key provided.")
        self.api_key = os.environ['API_KEY']
        self.required_header = []

    @middleware
    async def middleware(self, request, handler):
        """Process the request.

        request: Add X-Riot-Token Header with the API Key.
        response: No changes.
        """
        headers = dict(request.headers)
        headers.update({'X-Riot-Token': self.api_key})
        url = str(request.url)
        request = request.clone(headers=headers, rel_url=url.replace("http:", "https:"))
        return await handler(request)


class Logging:
    """Periodically save data to file."""

    def __init__(self):
        if not "SERVER" in os.environ:
            raise Exception("No Server provided.")
        self.server = os.environ["SERVER"]
        self.count = {}
        self.worker = threading.Thread(target=self.worker)
        self.worker.run()
        self.stopped = False

    def down(self):
        """Set thread to stop at next cycle."""
        self.stop = True
        self.worker.join()

    def worker(self):
        """Save data to file."""
        while not self.stopped:
            to_write = []
            current_second = datetime.now().timestamp() // 1000
            for target in self.count:
                for second in self.count[target]:
                    if second < current_second:
                        to_write.append([second, self.count[target][second]])
                        del self.count[target][second]
            with open(f"logs/{self.server}_proxy.log", 'a+') as logfile:
                for entry in to_write:
                    logfile.write("-".join(entry))

    @middleware
    async def middleware(self, request, handler):
        """Read url. Add to log."""
        url = str(request.url).split("/lol/")[1]
        target = "-".join(url.split("/")[:3])
        current_second = datetime.now().timestamp() // 1000
        if target not in self.count:
            self.count[target] = {}

        if current_second not in self.count[target]:
            self.count[target][current_second] = 1
        else:
            self.count[target][current_second] += 1
        return await handler(request)
