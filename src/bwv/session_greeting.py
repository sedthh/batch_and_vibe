"""One greeting per observed game session, independent of temporary control loss."""


SESSION_GREETING_GUIDANCE = """A session_event in the CURRENT message requests one brief in-character
greeting. connection_started means the mod first connected; it does not prove
the game was just loaded. world_session_changed means the engine's script session
changed through initialization, loading, restoration or reboot; it does not prove
which save or island was loaded, or that the player was away. Do not mention these
technical details in speech. Greet your friend/master naturally in first person.
You may add one short recollection grounded in saved memory or conversation
history. If there is no useful history, just be pleased to see the player; do not
invent shared events, completed goals, time spent apart, or activity while the
game was closed. Current observations take precedence, since history may belong
to another save. Never replay a past action just because you recalled it.
This greeting is an exception to silence on routine updates. Use a short answer
or say, not a long recap or a follow-up question. If God also asks a question,
prioritize answering it and optionally blend in the greeting. Historical
session_event entries do not request another greeting."""


class SessionGreeting:
    def __init__(self):
        self.session = None
        self.pending = None

    def observe(self, session):
        if session != self.session:
            self.pending = 'connection_started' if self.session is None else 'world_session_changed'
            self.session = session

    def accepted(self, session):
        if session == self.session:
            self.pending = None
