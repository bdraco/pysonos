# -*- coding: utf-8 -*-
# pylint: disable=not-context-manager,import-error,wrong-import-position

# NOTE: The pylint not-content-manager warning is disabled pending the fix of
# a bug in pylint. See https://github.com/PyCQA/pylint/issues/782

# Disable while we have Python 2.x compatability
# pylint: disable=useless-object-inheritance


"""Classes to handle Sonos UPnP Events and Subscriptions.

The `Subscription` class from this module will be used in
:py:mod:`soco.services` if `config.EVENTS_MODULE` is set
to point to this module.

Example:

    Run this code, and change your volume, tracks etc::

        from __future__ import print_function
        import logging
        logging.basicConfig()
        import soco
        from pprint import pprint

        from soco import events_asyncio
        soco.config.EVENTS_MODULE = events_asyncio
        from twisted.internet import reactor

        def print_event(event):
            try:
                pprint (event.variables)
            except Exception as e:
                pprint ('There was an error in print_event:', e)

        def main():
            # pick a device at random and use it to get
            # the group coordinator
            device = soco.discover().pop().group.coordinator
            print (device.player_name)
            sub = device.renderingControl.subscribe().subscription
            sub2 = device.avTransport.subscribe().subscription
            sub.callback = print_event
            sub2.callback = print_event

            def before_shutdown():
                sub.unsubscribe()
                sub2.unsubscribe()
                events_twisted.event_listener.stop()

            reactor.addSystemEventTrigger(
                'before', 'shutdown', before_shutdown)

        if __name__=='__main__':
            reactor.callWhenRunning(main)
            reactor.run()

.. _Deferred: https://twistedmatrix.com/documents/current/api/\
twisted.internet.defer.Deferred.html
.. _Failure: https://twistedmatrix.com/documents/current/api/\
twisted.python.failure.Failure.html

"""

from __future__ import unicode_literals

import sys
import logging
import socket

# Hack to make docs build without twisted installed
if "sphinx" in sys.modules:

    class Resource(object):  # pylint: disable=no-init
        """Fake Resource class to use when building docs"""


else:
    import asyncio
    import aiohttp


# Event is imported for compatibility with events.py
# pylint: disable=unused-import
from .events_base import Event  # noqa: F401

from .events_base import (  # noqa: E402
    EventNotifyHandlerBase,
    EventListenerBase,
    SubscriptionBase,
    SubscriptionsMap,
)

from .exceptions import SoCoException  # noqa: E402

log = logging.getLogger(__name__)  # pylint: disable=C0103


class EventNotifyHandler(EventNotifyHandlerBase):
    """Handles HTTP ``NOTIFY`` Verbs sent to the listener server.
    Inherits from `soco.events_base.EventNotifyHandlerBase`.
    """

    def __init__(self):
        super().__init__()
        # The SubscriptionsMapTwisted instance created when this module is
        # imported. This is referenced by
        # soco.events_base.EventNotifyHandlerBase.
        self.subscriptions_map = subscriptions_map

    async def notify(self, request):  # pylint: disable=invalid-name
        """Serve a ``NOTIFY`` request by calling `handle_notification`
        with the headers and content.
        """
        self.handle_notification(request.headers, await request.text())
        return aiohttp.web.Response(text="OK", status=200)

    # pylint: disable=no-self-use, missing-docstring
    def log_event(self, seq, service_id, timestamp):
        log.info("Event %s received for %s service at %s", seq, service_id, timestamp)


class EventListener(EventListenerBase):
    """The Event Listener.

    Runs an http server which is an endpoint for ``NOTIFY``
    requests from Sonos devices. Inherits from
    `soco.events_base.EventListenerBase`.
    """

    def __init__(self):
        super().__init__()
        self.sock = None
        self.port = None
        self.runner = None
        self.site = None

    def start(self, any_zone):
        """Start the event listener listening on the local machine.

        Args:
            any_zone (SoCo): Any Sonos device on the network. It does not
                matter which device. It is used only to find a local IP
                address reachable by the Sonos net.

        """
        return self._start(any_zone)

    def listen(self, ip_address):
        """Start the event listener listening on the local machine at
        port 1400 (default). If this port is unavailable, the
        listener will attempt to listen on the next available port,
        within a range of 100.

        Make sure that your firewall allows connections to this port.

        This method is called by `soco.events_base.EventListenerBase.start`

        Handling of requests is delegated to an instance of the
        `EventNotifyHandler` class.

        Args:
            ip_address (str): The local network interface on which the server
                should start listening.
        Returns:
            int: The port on which the server is listening.

        Note:
            The port on which the event listener listens is configurable.
            See `config.EVENT_LISTENER_PORT`
        """
        for port_number in range(
            self.requested_port_number, self.requested_port_number + 100
        ):
            try:
                if port_number > self.requested_port_number:
                    log.warning("Trying next port (%d)", port_number)
                # pylint: disable=no-member
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind((ip_address, port_number))
                sock.listen(200)
                self.sock = sock
                self.port = port_number
                break
            # pylint: disable=invalid-name
            except socket.error as e:
                log.warning(e)
                continue

        if not self.port:
            return None

        async def _async_start_site():
            handler = EventNotifyHandler()
            app = aiohttp.web.Application()
            app.add_routes([aiohttp.web.route("notify", "*", handler.notify)])
            self.runner = aiohttp.web.AppRunner(app)
            await self.runner.setup()
            self.site = aiohttp.web.SockSite(self.runner, self.sock)
            await self.site.start()

        asyncio.create_task(_async_start_site())
        log.info("Event listener running on %s", (ip_address, self.port))
        return self.port

    # pylint: disable=unused-argument
    def stop_listening(self, address):
        """Stop the listener."""

        async def _async_stop_site():
            await self.site.stop()
            await self.runner.cleanup()
            self.port = None

        asyncio.create_task(_async_stop_site)


class Subscription(SubscriptionBase):
    """A class representing the subscription to a UPnP event.
    Inherits from `soco.events_base.SubscriptionBase`.
    """

    def __init__(self, service, callback=None, session=None):
        """
        Args:
            service (Service): The SoCo `Service` to which the subscription
                 should be made.
            event_queue (:class:`~queue.Queue`): A queue on which received
                events will be put. If not specified, a queue will be
                created and used.

        """
        super().__init__(service, None)
        #: :py:obj:`function`: callback function to be called whenever an
        #: `Event` is received. If it is set and is callable, the callback
        #: function will be called with the `Event` as the only parameter and
        #: the Subscription's event queue won't be used.
        self.callback = callback
        # The SubscriptionsMapTwisted instance created when this module is
        # imported. This is referenced by soco.events_base.SubscriptionBase.
        self.subscriptions_map = subscriptions_map
        # The EventListener instance created when this module is imported.
        # This is referenced by soco.events_base.SubscriptionBase.
        self.event_listener = event_listener
        # Used to keep track of the auto_renew loop
        self._auto_renew_loop = None
        # Used to serialise calls to subscribe, renew and unsubscribe
        self._client_session = session or aiohttp.ClientSession()

    # pylint: disable=arguments-differ
    def subscribe(self, requested_timeout=None, auto_renew=False, strict=True):
        """Subscribe to the service.

        If requested_timeout is provided, a subscription valid for that number
        of seconds will be requested, but not guaranteed. Check
        `timeout` on return to find out what period of validity is
        actually allocated.

        This method calls `events_base.SubscriptionBase.subscribe`.

        Note:
            SoCo will try to unsubscribe any subscriptions which are still
            subscribed on program termination, but it is good practice for
            you to clean up by making sure that you call :meth:`unsubscribe`
            yourself.

        Args:
            requested_timeout(int, optional): The timeout to be requested.
            auto_renew (bool, optional): If `True`, renew the subscription
                automatically shortly before timeout. Default `False`.

        Returns:
            The result of the subscription

        """
        self.subscriptions_map.subscribing()
        future = asyncio.Future()
        subscribe = super().subscribe

        def _wrap_action():
            try:
                subscribe(requested_timeout, auto_renew)
                future.set_result(self)
            except SoCoException as ex:
                future.set_exception(ex)
            except Exception as ex:  # pylint: disable=broad-except
                msg = (
                    "An Exception occurred. Subscription to"
                    + " {}, sid: {} has been cancelled".format(
                        self.service.base_url + self.service.event_subscription_url,
                        self.sid,
                    )
                )
                log.exception(msg)
                self._cancel_subscription(ex)
                future.set_exception(ex)
            finally:
                self.subscriptions_map.finished_subscribing()

        asyncio.get_running_loop().call_soon(_wrap_action)
        return future

    async def renew(
        self, requested_timeout=None, is_autorenew=False, strict=True
    ):  # pylint: disable=invalid-overridden-method
        """renew(requested_timeout=None)
        Renew the event subscription.
        You should not try to renew a subscription which has been
        unsubscribed, or once it has expired.

        This method calls `events_base.SubscriptionBase.renew`.

        Args:
            requested_timeout (int, optional): The period for which a renewal
                request should be made. If None (the default), use the timeout
                requested on subscription.
            is_autorenew (bool, optional): Whether this is an autorenewal.
                Default `False`.

        Returns:
            The result of the renew

        """
        try:
            return await self._wrap(super().renew, requested_timeout, is_autorenew)
        except Exception as exc:  # pylint: disable=broad-except
            msg = (
                "An Exception occurred. Subscription to"
                + " {}, sid: {} has been cancelled".format(
                    self.service.base_url + self.service.event_subscription_url,
                    self.sid,
                )
            )
            log.exception(msg)
            self._cancel_subscription(msg)
            if self.auto_renew_fail is not None:
                if hasattr(self.auto_renew_fail, "__call__"):
                    # pylint: disable=not-callable
                    self.auto_renew_fail(exc)
            raise

    async def unsubscribe(
        self, strict=True
    ):  # pylint: disable=invalid-overridden-method
        """unsubscribe()
        Unsubscribe from the service's events.
        Once unsubscribed, a Subscription instance should not be reused

        This method calls `events_base.SubscriptionBase.unsubscribe`.

        Args:
            strict (bool, optional): If True and an Exception occurs during
                execution, the returned Deferred_ will fail with a Failure_
                which will be passed to the applicable errback (if any has
                been set by the calling code) or, if False, the Failure will
                be logged and the Subscription instance will be passed to
                the applicable callback (if any has
                been set by the calling code). Default `True`.

        Returns:
            The result of the unsubscribe

        """
        return await self._wrap(super().unsubscribe)

    def _auto_renew_start(self, interval):
        """Starts the auto_renew loop."""
        self._auto_renew_loop = asyncio.get_running_loop().call_later(
            interval, self._auto_renew_run, interval
        )

    def _auto_renew_run(self, interval):
        asyncio.create_task(self.renew(is_autorenew=True))
        self._auto_renew_start(interval)

    def _auto_renew_cancel(self):
        """Cancels the auto_renew loop"""
        if self._auto_renew_loop:
            self._auto_renew_loop.cancel()
            self._auto_renew_loop = None

    # pylint: disable=no-self-use, too-many-branches, too-many-arguments
    def _request(self, method, url, headers, success):
        """Sends an HTTP request.

        Args:
            method (str): 'SUBSCRIBE' or 'UNSUBSCRIBE'.
            url (str): The full endpoint to which the request is being sent.
            headers (dict): A dict of headers, each key and each value being
                of type `str`.
            success (function): A function to be called if the
                request succeeds. The function will be called with a dict
                of response headers as its only parameter.

        """

        async def _make_request():
            response = await self._client_session.request(method, url, headers)
            if response.ok:
                success(response.headers)

        return _make_request

    async def _wrap(self, method, *args):

        """Wrap a call into an awaitable."""
        future = asyncio.Future()

        def _wrap_action():
            try:
                future.set_result(method(*args))
            except Exception as ex:  # pylint: disable=broad-except
                future.set_exception(ex)

        asyncio.get_running_loop().call_soon(_wrap_action)
        return await future


class SubscriptionsMapAio(SubscriptionsMap):
    """Maintains a mapping of sids to `soco.events_twisted.Subscription`
    instances. Registers each subscription to be unsubscribed at exit.

    Inherits from `soco.events_base.SubscriptionsMap`.
    """

    def __init__(self):
        super().__init__()
        # A counter of calls to Subscription.subscribe
        # that have started but not completed. This is
        # to prevent the event listener from being stopped prematurely
        self._pending = 0

    def register(self, subscription):
        """Register a subscription by updating local mapping of sid to
        subscription and registering it to be unsubscribed at exit.

        Args:
            subscription(`soco.events_twisted.Subscription`): the subscription
                to be registered.

        """

        # Add the subscription to the local dict of subscriptions so it
        # can be looked up by sid
        self.subscriptions[subscription.sid] = subscription

    def subscribing(self):
        """Called when the `Subscription.subscribe` method
        commences execution.
        """
        # Increment the counter
        self._pending += 1

    def finished_subscribing(self):
        """Called when the `Subscription.subscribe` method
        completes execution.
        """
        # Decrement the counter
        self._pending -= 1

    @property
    def count(self):
        """
        `int`: The number of active or pending subscriptions.
        """
        return len(self.subscriptions) + self._pending


subscriptions_map = SubscriptionsMapAio()  # pylint: disable=C0103
event_listener = EventListener()  # pylint: disable=C0103
