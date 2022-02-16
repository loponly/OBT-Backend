import logging
import os
import sys
import os
from functools import partial
import time
from .db import Fanout, DillDisk


def touch_recursive(target, path=['root']):
    if len(path) < 1:
        return target

    target[path[0]] = target.get(path[0], {})
    return touch_recursive(target[path[0]], path[1:])


def walk_tree(tree, func, prefix=[]):
    for k in tree:
        path = [*prefix, k]
        func(path, tree)
        walk_tree(tree[k], func, prefix=path)

def isorderedsublist(subset,superset):
    assert type(subset) == list and type(superset) == list, "List func"
    if subset == superset:
        return True

    for i, k in enumerate(superset):
        # Current item not indexed, so everything succeeded until now
        if i >= len(subset):
            return True

        if k != subset[i]:
            return False
    return False 

# Reverse walk (start at the bottom)
def rwalk_tree(tree, func, prefix=[]):
    for k in list(tree): # Copy keys, for modification in func
        path = [*prefix, k]
        rwalk_tree(tree[k], func, prefix=path)
        func(path, tree[k])


class RotatingFanoutHandler(logging.Handler):
    db = Fanout(directory=os.path.abspath('store/logs'), disk=DillDisk)

    def __init__(self):
        super().__init__()

    def emit(self, record):
        ldb = self.db.cache(record.name)
        with ldb.transact(retry=True):
            prev = ldb.get(record.created, [], retry=True) or []
            prev.extend([{'msg': record.msg, 'level': record.levelname, 'process': record.process}])
            ldb[record.created] = prev

        with self.db.transact(retry=True):
            config = self.db.get('config', {})
            tree = config.get('tree', {})
            leaf = touch_recursive(tree, record.name.split('.'))
            config['tree'] = tree
            self.db['config'] = config

    # def recompress(self):
    # TODO: stream a zstd dictionary (DillCache-v2) then recompress all records (long operation)

    def search_leaf(self, leaf_name: str):
        tree = self.db['config'].get('tree', {})
        paths = []

        def _check(path, _):
            fullpath = '.'.join(path)
            if fullpath.endswith(leaf_name):
                paths.append(fullpath)

        walk_tree(tree, _check)
        return paths

    def volume(self):
        return self.db.volume()

    def remove(self, spath: str) -> int:
        pathcomps = spath.split('.')
        config = self.db['config']

        def _remove(path, subtree):
            # Path is a child or root of the path we want to remove
            if isorderedsublist(pathcomps, path):
                print(f"Removing {path} {list(subtree.keys())}")
                ldb = self.db.cache('.'.join(path))
                ldb.clear(retry=True)

                # Delete already cleared leafs
                for k in list(subtree):
                    del subtree[k]

        rwalk_tree(config['tree'], _remove)
        self.db['config'] = config 


    def prune(self, store_for=30*24*60*60):
        tree = self.db['config'].get('tree', {})
        walk_tree(tree, partial(self._prune_leaf, store_for=store_for))

    def _prune_leaf(self, path, store_for=30*24*60*60):
        ctime = time.time() - store_for
        ldb = self.db.cache('.'.join(path))
        for k in list(ldb.iterkeys()):
            if k < ctime:
                del ldb[k]


def getLogger(name, propagate=False):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = propagate
    logger.addHandler(RotatingFanoutHandler())
    stdout = logging.StreamHandler(stream=sys.stdout)
    stdout.formatter = logging.Formatter('%(levelname)s:%(name)s:%(message)s')
    logger.addHandler(stdout)
    return logger
