# -*- coding: utf-8 -*-
# pylint: disable=not-context-manager,useless-object-inheritance

# NOTE: The pylint not-content-manager warning is disabled pending the fix of
# a bug in pylint https://github.com/PyCQA/pylint/issues/782

# NOTE: useless-object-inheritance needed for Python 2.x compatability

"""This module contains the classes underlying SoCo's caching system."""

from __future__ import unicode_literals

import threading
from time import time

from . import config
from .compat import dumps


class _BaseCache(object):

    """An abstract base class for the cache."""

    # pylint: disable=no-self-use, unused-argument

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._cache = {}
        #: `bool`: whether the cache is enabled
        self.enabled = True

    def put(self, item, *args, **kwargs):
        """Put an item into the cache."""
        raise NotImplementedError

    def get(self, *args, **kwargs):
        """Get an item from the cache."""
        raise NotImplementedError

    def delete(self, *args, **kwargs):
        """Delete an item from the cache."""
        raise NotImplementedError

    def clear(self):
        """Empty the whole cache."""
        raise NotImplementedError


class NullCache(_BaseCache):

    """A cache which does nothing.

    Useful for debugging.
    """

    def put(self, item, *args, **kwargs):
        """Put an item into the cache."""

    def get(self, *args, **kwargs):
        """Get an item from the cache."""
        return None

    def delete(self, *args, **kwargs):
        """Delete an item from the cache."""

    def clear(self):
        """Empty the whole cache."""


class TimedCache(_BaseCache):

    """A simple thread-safe cache for caching method return values.

    The cache key is generated by from the given ``*args`` and ``**kwargs``.
    Items are expired from the cache after a given period of time.

    Example:
        >>> from time import sleep
        >>> cache = TimedCache()
        >>> cache.put("item", 'some', kw='args', timeout=3)
        >>> # Fetch the item again, by providing the same args and kwargs.
        >>> assert cache.get('some', kw='args') == "item"
        >>> # Providing different args or kwargs will not return the item.
        >>> assert not cache.get('some', 'otherargs') == "item"
        >>> # Waiting for less than the provided timeout does not cause the
        >>> # item to expire.
        >>> sleep(2)
        >>> assert cache.get('some', kw='args') == "item"
        >>> # But waiting for longer does.
        >>> sleep(2)
        >>> assert not cache.get('some', kw='args') == "item"

    Warning:
        At present, the cache can theoretically grow and grow, since entries
        are not automatically purged, though in practice this is unlikely
        since there are not that many different combinations of arguments in
        the places where it is used in SoCo, so not that many different
        cache entries will be created. If this becomes a problem,
        use a thread and timer to purge the cache, or rewrite this to use
        LRU logic!
    """

    def __init__(self, default_timeout=0):
        """
        Args:
            default_timeout (int): The default number of seconds after
            which items will be expired.
        """
        super().__init__()
        #: `int`: The default caching expiry interval in seconds.
        self.default_timeout = default_timeout
        # A thread lock for the cache
        self._cache_lock = threading.Lock()

    def get(self, *args, **kwargs):
        """Get an item from the cache for this combination of args and kwargs.

        Args:
            *args: any arguments.
            **kwargs: any keyword arguments.

        Returns:
            object: The object which has been found in the cache, or `None` if
            no unexpired item is found. This means that there is no point
            storing an item in the cache if it is `None`.

        """
        if not self.enabled:
            return None
        # Look in the cache to see if there is an unexpired item. If there is
        # we can just return the cached result.
        cache_key = self.make_key(args, kwargs)
        # Lock and load
        with self._cache_lock:
            if cache_key in self._cache:
                expirytime, item = self._cache[cache_key]

                if expirytime >= time():
                    return item
                else:
                    # An expired item is present - delete it
                    del self._cache[cache_key]
        # Nothing found
        return None

    def put(self, item, *args, **kwargs):
        """Put an item into the cache, for this combination of args and kwargs.

        Args:
            *args: any arguments.
            **kwargs: any keyword arguments. If ``timeout`` is specified as one
                 of the keyword arguments, the item will remain available
                 for retrieval for ``timeout`` seconds. If ``timeout`` is
                 `None` or not specified, the ``default_timeout`` for this
                 cache will be used. Specify a ``timeout`` of 0 (or ensure that
                 the ``default_timeout`` for this cache is 0) if this item is
                 not to be cached.
        """
        if not self.enabled:
            return
        # Check for a timeout keyword, store and remove it.
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            timeout = self.default_timeout
        cache_key = self.make_key(args, kwargs)
        # Store the item, along with the time at which it will expire
        with self._cache_lock:
            self._cache[cache_key] = (time() + timeout, item)

    def delete(self, *args, **kwargs):
        """Delete an item from the cache for this combination of args and
        kwargs."""
        cache_key = self.make_key(args, kwargs)
        with self._cache_lock:
            try:
                del self._cache[cache_key]
            except KeyError:
                pass

    def clear(self):
        """Empty the whole cache."""
        with self._cache_lock:
            self._cache.clear()

    @staticmethod
    def make_key(*args, **kwargs):
        """Generate a unique, hashable, representation of the args and kwargs.

        Args:
            *args: any arguments.
            **kwargs: any keyword arguments.

        Returns:
            str: the key.
        """
        # This is not entirely straightforward, since args and kwargs may
        # contain mutable items and unicode. Possibilities include using
        # __repr__, frozensets, and code from Py3's LRU cache. But pickle
        # works, and although it is not as fast as some methods, it is good
        # enough at the moment
        cache_key = dumps((args, kwargs))
        return cache_key


class Cache(NullCache):

    """A factory class which returns an instance of a cache subclass.

    A `TimedCache` is returned, unless `config.CACHE_ENABLED` is `False`,
    in which case a `NullCache` will be returned.
    """

    def __new__(cls, *args, **kwargs):
        if config.CACHE_ENABLED:
            new_cls = TimedCache
        else:
            new_cls = NullCache
        instance = super(Cache, cls).__new__(new_cls)
        instance.__init__(*args, **kwargs)
        return instance
