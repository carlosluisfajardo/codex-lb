# Return an explicit HTTP denial for late WebSocket arrivals

A pre-handshake close1013 during graceful drain becomes an opaque failed upgrade.
Port the scoped upstream #2255 behavior through the existing HTTP-denial helper:
503, Retry-After5, and the existing proxy or generic error envelope. Retain the
register-before-barrier ordering, accepted socket counter, terminal delivery,
request log, reservation settlement and accepted connection close1012.

This is source and hermetic test work. No live drain, listener or LB replacement
is part of this change.
