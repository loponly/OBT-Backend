# Only python3 std libraries allowed here

import traceback
import inspect
from hashlib import blake2b
from base64 import b64encode
from functools import wraps


def assert_type(x, t, m):
    if type(t) == tuple:
        assert type(x) in t, "'%s' needs to be %s (is %s)" % (
            m,
            ','.join(tp.__name__ for tp in t),
            type(x).__name__,
        )

    else:
        assert type(x) == t, "'%s' needs to be %s (is %s)" % (m, t.__name__, type(x).__name__)


def no_except(f, *args, **kwargs):
    try:
        return f(*args, **kwargs)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return None


def safe_del(d, k):
    if k in d:
        del d[k]


def editable_keys(x): return list(
    filter(lambda k: '__' not in k and type(x[k]) in [dict, str, list], x)
)


def getr(d: dict, k: str, default=None):
    """
    Gets item at d[k.split('.')[0]][d.split('.')[1]][...etc], returning the default if it does not existis 
    Created to replace the a.get('b',{}).get('c',{}).get('d',{}) pattern
    """
    # TODO: optimize str operations (_getr w/ stack)
    path = k.split('.')
    if len(path) > 1:
        if path[0] not in d:
            return default

        # TODO: check if d[path[0]] is indexable?
        return getr(d[path[0]], '.'.join(path[1:]), default)
    return d.get(k, default)


def applyr(d: dict, k: str, f, default=None):
    """
    Applies d[k.split('.')[0]][d.split('.')[1]][...etc] = f(d[..][..]... or default), and creates a new entry if it doesn't exist
    """
    path = k.split('.')
    if len(path) > 1:
        d[path[0]] = d.get(path[0], {})
        return applyr(d[path[0]], '.'.join(path[1:]), f, default)

    d[k] = f(d.get(k, default))


def incr(d: dict, k: str, i: float = 1):
    """
    Adds `i` to d[k.split('.')[0]][d.split('.')[1]][...etc], and creates new entry if it doesn't exist
    """
    return applyr(d, k, lambda v: v + i, 0)


def map_dict(f, v: dict):
    args = inspect.getargspec(f).args
    if len(args) <= 1:
        return dict(zip(v.keys(), map(f, v.values())))
    else:
        def _expand_args(args):
            return f(*args)

        return dict(map(_expand_args, v.items()))

# . !A || B (Asymetric gate)
# . only require B if A is set


def imply(a, b): return not a or b


def atomic_memoize(db, func, *args, _overwrite=False, _expire=24*60*60, **kwargs):
    cached_f = db.memoize(expire=_expire, tag=func.__name__)(func)
    cache_key = cached_f.__cache_key__(*args, **kwargs)
    processing_key = ('processing',) + cache_key
    if db.get(processing_key):
        res = db.get(cache_key, retry=True)
        if res:
            return res
        raise AssertionError("Building up statistics, come back later")

    if cache_key not in db or _overwrite:
        try:
            db.set(processing_key, True, expire=10 * 60, retry=True)
            result = func(*args, **kwargs)
            db.set(cache_key, result, expire=_expire, tag=func.__name__, retry=True)
        finally:
            db.delete(processing_key, retry=True)
    return cached_f(*args, **kwargs)


def get_short_hash(username: str, salt: bytes = None):
    blk = blake2b(salt=salt)
    blk.update(username.encode())
    return b64encode(blk.digest())[0:16].decode()

