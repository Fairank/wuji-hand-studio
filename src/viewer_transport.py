"""Keep original Viewer endpoints inside browser-supported loopback ports."""
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

# Chromium net/base/port_util.cc (Fetch port blocking), no browser exceptions.
BLOCKED=frozenset((0,1,7,9,11,13,15,17,19,20,21,22,23,25,37,42,43,53,69,77,79,87,95,
    101,102,103,104,109,110,111,113,115,117,119,123,135,137,139,143,161,179,389,427,
    465,512,513,514,515,526,530,531,532,540,548,554,556,563,587,601,636,989,990,993,
    995,1719,1720,1723,2049,3659,4045,5060,5061,6000,6566,6665,6666,6667,6668,6669,6697,10080))


class ViewerTransport:
    def __init__(self,value,relay_factory=None):
        from official_visualization import viewer_url
        value=viewer_url(value)  # Validate both exact loopback endpoints first.
        self.source=value;self.relays=[];self.url=None;self.relay_factory=relay_factory

    def open(self):
        if self.url:return self.url
        if self.relays:raise ValueError('Previous relay cleanup remains unconfirmed')
        value=self.source;relay_factory=self.relay_factory
        if relay_factory is None:
            from loopback_stream_relay import StreamRelay
            relay_factory=StreamRelay
        def supported(parsed):
            if parsed.port not in BLOCKED:return parsed
            for _ in range(8):
                relay=relay_factory(parsed.port);self.relays.append(relay)
                if type(relay.port) is int and relay.port not in BLOCKED and 1024<=relay.port<=65535:
                    return parsed._replace(netloc='127.0.0.1:'+str(relay.port))
                if not relay.close(timeout=5):raise ValueError('Relay port cleanup remains unconfirmed')
                self.relays.remove(relay)
            raise ValueError('No browser-supported loopback relay port available')
        try:
            outer=urlsplit(value);pairs=parse_qsl(outer.query,keep_blank_values=True)
            pairs=[(key,urlunsplit(supported(urlsplit(val))) if key=='url' else val) for key,val in pairs]
            outer=supported(outer)._replace(query=urlencode(pairs))
            from official_visualization import viewer_url
            self.url=viewer_url(urlunsplit(outer))
            return self.url
        except BaseException:
            self.close();raise

    @property
    def forwarded(self):return bool(self.relays)

    def close(self):
        remaining=[]
        for relay in self.relays:
            try:closed=relay.close(timeout=5)
            except Exception:closed=False
            if not closed:remaining.append(relay)
        self.relays=remaining
        if not remaining:self.url=None
        return not remaining
