import os
import sys
import re
import collections


def lookahead(iterable):
    """Pass through all values from the given iterable, augmented by the
    information if there are more values to come after the current one
    (True), or if it is the last value (False).
    """
    # Get an iterator and pull the first value.
    it = iter(iterable)
    last = next(it)
    # Run the iterator to exhaustion (starting from the second value).
    for val in it:
        # Report the *previous* value (more to come).
        yield last, True
        last = val
    # Report the last value.
    yield last, False


def safe_list_get(l, idx, default):
    try:
        return l[idx]
    except IndexError:
        return default


def add_pkg():
    abs_path = os.path.realpath('..')
    if abs_path not in sys.path:
        sys.path.append(abs_path)


def candletype_from_span(timespan):
    time_map = {"%dw": 7 * 24 * 60 * 60, "%dd": 24 * 60 * 60, "%dh": 60 * 60, "%dm": 60, "%ds": 1}
    for t, value in time_map.items():
        if timespan // time_map[t] > 0:
            return t % (timespan / value)


def span_from_candletype(candletype):
    time_map = {'w': 7 * 24 * 60 * 60, 'd': 24 * 60 * 60, 'h': 60 * 60, 'm': 60, 's': 1}
    match = re.search(r"([0-9]+)([a-z]{1})", candletype)
    base = int(match.group(1))
    exp = time_map[match.group(2)]
    return base * exp


def to_internal_market(market: str):
    return ''.join(market.split(':'))

# https://github.com/titouandk/emitter.py


class EventEmitter:
    # special event
    ERROR = object()

    def __init__(self):
        super().__init__()
        self._events = {}

    def on(self, event, listener, once=False):
        # sanitize arguments types and values

        if event is None:
            raise ValueError("event cannot be None")

        if not callable(listener):
            raise TypeError("listener must be callable")

        once = bool(once)

        # create event (if it does not exists yet)
        if event not in self._events:
            self._events[event] = collections.OrderedDict()

        # add listener to the event (along with its configuration)
        # update listener config if listener is already registered
        self._events[event].update({listener: {"once": once}})

        return True

    def once(self, event, listener):
        return self.on(event, listener, once=True)

    def off(self, event=None, listener=None):
        # if no event given, remove all events
        if event is None:
            self._events = {}
            return True

        # if user tries to remove a non-existent event
        if self._events.get(event) is None:
            # we return True, since the event emitter is in
            # a state the user want it to be (no event x in the emitter)
            return True

        # delete all listeners for the given event
        if listener is None:
            del self._events[event]
            return True

        # if user tries to remove a non-existent listener
        if self._events[event].get(listener) is None:
            # we return True, since the event emitter is in
            # a state the user want it to be (no listener y for the event x)
            return True

        # delete the listener after detach event has been sent
        del self._events[event][listener]

        # if no more listeners in the given event, delete it
        if len(self._events[event]) == 0:
            del self._events[event]

        return True

    def events(self):
        # return a new set, containing events
        return set(self._events.keys())

    def listeners(self, event):
        # return a new list, containing listeners of the given event
        return list(self._events.get(event, []))

    def emit(self, event, *args, **kwargs):
        # if user tries to emit the None event
        if event is None:
            return False

        # if user tries to emits a non-existent event
        if self._events.get(event) is None:
            return False

        # trigger each listener attached to the event
        # we iterate on a copy to be allowed to mutate the
        # OrderedDict during iteration
        for listener in list(self._events[event]):
            try:
                # trigger the current listener, which is a
                # callback given by the user (it can raise any errors)
                listener(*args, **kwargs)
            except SystemExit:
                raise
            except:
                # if the exception occurred during error handling, stop here
                if event is self.ERROR:
                    raise

                # emit error event, passing error data as first arg
                self.emit(self.ERROR, sys.exc_info(), *args, **kwargs)
            finally:
                # remove listener if it was a one-shot
                if self._events[event][listener]["once"]:
                    self.off(event, listener)

        return True
