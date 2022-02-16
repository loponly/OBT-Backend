from tempfile import gettempdir
from diskcache import Disk, Cache, FanoutCache, EVICTION_POLICY
import hashlib
import portalocker
import dill
import zstd
import time
import os.path as op


class Constant(tuple):
    "Pretty display of immutable constant."
    def __new__(cls, name):
        return tuple.__new__(cls, (name,))

    def __repr__(self):
        return '%s' % self[0]


UNKNOWN = Constant('UNKNOWN')


class Fanout(FanoutCache):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._disk = kwargs.get('disk', Disk)

    def cache(self, name):
        """Return Cache with given `name` in subdirectory.
        >>> fanout_cache = FanoutCache()
        >>> cache = fanout_cache.cache('test')
        >>> cache.set('abc', 123)
        True
        >>> cache.get('abc')
        123
        >>> len(cache)
        1
        >>> cache.delete('abc')
        True
        :param str name: subdirectory name for Cache
        :return: Cache with given name
        """
        _caches = self._caches

        try:
            return _caches[name]
        except KeyError:
            parts = name.split('/')
            directory = op.join(self._directory, 'cache', *parts)
            temp = Cache(directory=directory, disk=self._disk, sqlite_synchronous=2)
            _caches[name] = temp
            return temp


class DillDisk(Disk):
    def __init__(self, directory, compress_level=3, **kwargs):
        self.compress = compress_level
        self.read = 0
        self.write = 0
        super().__init__(directory, **kwargs)

    def serialize(self, v):
        b = dill.dumps(v)
        # TODO: check sizeof dill serialization (error if too large)
        d = zstd.compress(b, self.compress, 1)
        return d

    def deserialize(self, v):
        return dill.loads(zstd.decompress(v))

    def store(self, value, read, key=UNKNOWN):
        if not read:
            value = self.serialize(value)
        self.write += len(value)
        return super().store(value, read)

    def unsafe_store(self, value, read, key=UNKNOWN):
        return super().store(value, read)

    def fetch(self, mode, filename, value, read):
        data = super().fetch(mode, filename, value, read)
        self.read += len(data)
        if not read:
            data = self.deserialize(data)
        return data

    def unsafe_fetch(self, mode, filename, value, read):
        return super().fetch(mode, filename, value, read)


class DillCache(Cache):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, disk=DillDisk, sqlite_synchronous=2, **kwargs)

    # TODO: force_unlock with boot check?

    def lock(self, key: str, timeout=1):
        lock_key = hashlib.blake2b(bytes(f'{self.directory}{key}', 'utf-8'), digest_size=16).digest().hex()
        return portalocker.Lock(op.join(gettempdir(), f'{lock_key}.lck'), mode='wb+', timeout=timeout)

    def unsafe_get(self, key, default=None, read=False, expire_time=False, tag=False,
                   retry=False):

        if not hasattr(self._disk, 'unsafe_store'):
            return self.set(key, value, expire=expire, read=read, tag=tag, retry=retry)

        db_key, raw = self._disk.put(key)
        update_column = EVICTION_POLICY[self.eviction_policy]['get']
        select = (
            'SELECT rowid, expire_time, tag, mode, filename, value'
            ' FROM Cache WHERE key = ? AND raw = ?'
            ' AND (expire_time IS NULL OR expire_time > ?)'
        )

        if expire_time and tag:
            default = (default, None, None)
        elif expire_time or tag:
            default = (default, None)

        if not self.statistics and update_column is None:
            # Fast path, no transaction necessary.

            rows = self._sql(select, (db_key, raw, time.time())).fetchall()

            if not rows:
                return default

            (rowid, db_expire_time, db_tag, mode, filename, db_value), = rows

            try:
                value = self._disk.unsafe_fetch(mode, filename, db_value, read)
            except IOError:
                # Key was deleted before we could retrieve result.
                return default

        else:  # Slow path, transaction required.
            cache_hit = (
                'UPDATE Settings SET value = value + 1 WHERE key = "hits"'
            )
            cache_miss = (
                'UPDATE Settings SET value = value + 1 WHERE key = "misses"'
            )

            with self._transact(retry) as (sql, _):
                rows = sql(select, (db_key, raw, time.time())).fetchall()

                if not rows:
                    if self.statistics:
                        sql(cache_miss)
                    return default

                (rowid, db_expire_time, db_tag,
                     mode, filename, db_value), = rows  # noqa: E127

                try:
                    value = self._disk.unsafe_fetch(mode, filename, db_value, read)
                except IOError as error:
                    if error.errno == errno.ENOENT:
                        # Key was deleted before we could retrieve result.
                        if self.statistics:
                            sql(cache_miss)
                        return default
                    else:
                        raise

                if self.statistics:
                    sql(cache_hit)

                now = time.time()
                update = 'UPDATE Cache SET %s WHERE rowid = ?'

                if update_column is not None:
                    sql(update % update_column.format(now=now), (rowid,))

        if expire_time and tag:
            return (value, db_expire_time, db_tag)
        elif expire_time:
            return (value, db_expire_time)
        elif tag:
            return (value, db_tag)
        else:
            return value

    def unsafe_set(self, key, value, expire=None, read=False, tag=None, retry=False):
        if not hasattr(self._disk, 'unsafe_store'):
            return self.set(key, value, expire=expire, read=read, tag=tag, retry=retry)

        now = time.time()
        db_key, raw = self._disk.put(key)
        expire_time = None if expire is None else now + expire
        size, mode, filename, db_value = self._disk.unsafe_store(value, read, key=key)
        columns = (expire_time, tag, size, mode, filename, db_value)

        with self._transact(retry, filename) as (sql, cleanup):
            rows = sql(
                'SELECT rowid, filename FROM Cache'
                ' WHERE key = ? AND raw = ?',
                (db_key, raw),
            ).fetchall()

            if rows:
                (rowid, old_filename), = rows
                cleanup(old_filename)
                self._row_update(rowid, now, columns)
            else:
                self._row_insert(db_key, raw, now, columns)

            self._cull(now, sql, cleanup)

            return True

    def __iter__(self):
        for item in super().__iter__():
            # Ignore meta values when iterating
            if isinstance(item, str) and item[:2] == '__' and item[-2:] == '__':
                continue
            else:
                yield item

    def __call__(self, key, x):
        doc = self[key]
        if callable(x):
            x(doc)
        else:
            doc[key] = x
        self[key] = doc
        return doc
