from textwrap import dedent

import requests

from TwitchChannelPointsMiner.classes.Settings import Events


class Webhook(object):
    __slots__ = ["endpoint", "method", "events"]

    def __init__(self, endpoint: str, method: str, events: list):
        self.endpoint = endpoint
        self.method = method
        self.events = [str(e) for e in events]

    def send(self, message: str, event: Events) -> None:

        if str(event) in self.events:
            if self.method.lower() == "get":
                requests.get(
                    url=self.endpoint,
                    params={"event_name": str(event), "message": message},
                )
            elif self.method.lower() == "post":
                # Send the message in the POST body. `body`/`title` make this
                # work with Apprise (and most webhook receivers); `event_name`
                # and `message` are kept for generic consumers.
                requests.post(
                    url=self.endpoint,
                    data={
                        "body": message,
                        "title": str(event),
                        "event_name": str(event),
                        "message": message,
                    },
                )
            else:
                raise ValueError("Invalid method, use POST or GET")
