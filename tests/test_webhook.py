import unittest.mock as mock

from TwitchChannelPointsMiner.classes.Webhook import Webhook
from TwitchChannelPointsMiner.classes.Settings import Events


def test_post_sends_body_for_apprise():
    wh = Webhook(
        endpoint="http://apprise:8000/notify/46",
        method="POST",
        events=[Events.STREAMER_ONLINE],
    )
    with mock.patch(
        "TwitchChannelPointsMiner.classes.Webhook.requests.post"
    ) as post:
        wh.send("hello", Events.STREAMER_ONLINE)
    assert post.call_count == 1
    kwargs = post.call_args.kwargs
    assert kwargs["url"] == "http://apprise:8000/notify/46"
    # Apprise requires the message in the body, not a query param
    assert kwargs["data"]["body"] == "hello"
    assert kwargs["data"]["title"] == str(Events.STREAMER_ONLINE)


def test_skips_unsubscribed_event():
    wh = Webhook(endpoint="http://x", method="POST", events=[Events.STREAMER_ONLINE])
    with mock.patch(
        "TwitchChannelPointsMiner.classes.Webhook.requests.post"
    ) as post:
        wh.send("hi", Events.STREAMER_OFFLINE)
    post.assert_not_called()


def test_get_uses_query_params():
    wh = Webhook(endpoint="http://x", method="GET", events=[Events.STREAMER_ONLINE])
    with mock.patch(
        "TwitchChannelPointsMiner.classes.Webhook.requests.get"
    ) as get:
        wh.send("hi", Events.STREAMER_ONLINE)
    assert get.call_args.kwargs["params"]["message"] == "hi"
