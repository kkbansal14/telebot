#!/usr/bin/env python
#
# A library that provides a Python interface to the Telegram Bot API
# Copyright (C) 2015-2022
# Leandro Toledo de Souza <devs@python-telegram-bot.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser Public License for more details.
#
# You should have received a copy of the GNU Lesser Public License
# along with this program.  If not, see [http://www.gnu.org/licenses/].
# pylint: disable=wrong-import-position,ungrouped-imports,wrong-import-order
"""This module contains the classes JobQueue and Job."""

import datetime
import weakref
from typing import TYPE_CHECKING, Optional, Tuple, Union, cast, overload

# We apply a small hack here to make AsyncIOScheduler/Executor work with
# class based callbacks. See https://github.com/agronholm/apscheduler/issues/583
# We just override aps.util.iscoroutinefunction, which is an imported function in that module
# For this to work, this must happen before any other import from APScheduler
# This is also the reason for the plyint disable at the top of the file and the
# flake8 setting in setup.cfg
# TODO: 1. Check if this works if the user manually imports APS stuff before we get here
#  2. Think about refactoring. `Job.__call__` was introduced in
#     https://github.com/python-telegram-bot/python-telegram-bot/pull/2692
#     but there are probably feasible alternatives.
from telegram._utils.asyncio import is_coroutine_function, run_non_blocking
from apscheduler import util

util.iscoroutinefunction = is_coroutine_function

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.job import Job as APSJob

from telegram._utils.types import JSONDict
from telegram.ext._extbot import ExtBot
from telegram.ext._utils.types import JobCallback

if TYPE_CHECKING:
    from telegram.ext import Dispatcher
    import apscheduler.job  # noqa: F401


class JobQueue:
    """This class allows you to periodically perform tasks with the bot. It is a convenience
    wrapper for the APScheduler library.

    Attributes:
        scheduler (:class:`apscheduler.schedulers.asyncio.AsyncIOScheduler`): The scheduler.
            ..versionchanged:: 14.0
                Use :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` instead of
                :class:`apscheduler.schedulers.background.BackgroundScheduler`


    """

    __slots__ = ('_dispatcher', 'scheduler')

    def __init__(self) -> None:
        self._dispatcher: 'Optional[weakref.ReferenceType[Dispatcher]]' = None
        self.scheduler = AsyncIOScheduler(timezone=pytz.utc)

    def _tz_now(self) -> datetime.datetime:
        return datetime.datetime.now(self.scheduler.timezone)

    @overload
    def _parse_time_input(self, time: None, shift_day: bool = False) -> None:
        ...

    @overload
    def _parse_time_input(
        self,
        time: Union[float, int, datetime.timedelta, datetime.datetime, datetime.time],
        shift_day: bool = False,
    ) -> datetime.datetime:
        ...

    def _parse_time_input(
        self,
        time: Union[float, int, datetime.timedelta, datetime.datetime, datetime.time, None],
        shift_day: bool = False,
    ) -> Optional[datetime.datetime]:
        if time is None:
            return None
        if isinstance(time, (int, float)):
            return self._tz_now() + datetime.timedelta(seconds=time)
        if isinstance(time, datetime.timedelta):
            return self._tz_now() + time
        if isinstance(time, datetime.time):
            date_time = datetime.datetime.combine(
                datetime.datetime.now(tz=time.tzinfo or self.scheduler.timezone).date(), time
            )
            if date_time.tzinfo is None:
                date_time = self.scheduler.timezone.localize(date_time)
            if shift_day and date_time <= datetime.datetime.now(pytz.utc):
                date_time += datetime.timedelta(days=1)
            return date_time
        # isinstance(time, datetime.datetime):
        return time

    def set_dispatcher(self, dispatcher: 'Dispatcher') -> None:
        """Set the dispatcher to be used by this JobQueue.

        Args:
            dispatcher (:class:`telegram.ext.Dispatcher`): The dispatcher.

        """
        self._dispatcher = weakref.ref(dispatcher)
        if isinstance(dispatcher.bot, ExtBot) and dispatcher.bot.defaults:
            self.scheduler.configure(timezone=dispatcher.bot.defaults.tzinfo or pytz.utc)

    @property
    def dispatcher(self) -> 'Dispatcher':
        """The dispatcher this JobQueue is associated with."""
        if self._dispatcher is None:
            raise RuntimeError('No dispatcher was set for this JobQueue.')
        dispatcher = self._dispatcher()
        if dispatcher is not None:
            return dispatcher
        raise RuntimeError('The dispatcher instance is no longer alive.')

    def run_once(
        self,
        callback: JobCallback,
        when: Union[float, datetime.timedelta, datetime.datetime, datetime.time],
        context: object = None,
        name: str = None,
        job_kwargs: JSONDict = None,
    ) -> 'Job':
        """Creates a new :class:`Job` instance that runs once and adds it to the queue.

        Args:
            callback (:obj:`callable`): The callback function that should be executed by the new
                job. Callback signature: ``def callback(context: CallbackContext)``
            when (:obj:`int` | :obj:`float` | :obj:`datetime.timedelta` |                         \
                  :obj:`datetime.datetime` | :obj:`datetime.time`):
                Time in or at which the job should run. This parameter will be interpreted
                depending on its type.

                * :obj:`int` or :obj:`float` will be interpreted as "seconds from now" in which the
                  job should run.
                * :obj:`datetime.timedelta` will be interpreted as "time from now" in which the
                  job should run.
                * :obj:`datetime.datetime` will be interpreted as a specific date and time at
                  which the job should run. If the timezone (:attr:`datetime.datetime.tzinfo`) is
                  :obj:`None`, the default timezone of the bot will be used.
                * :obj:`datetime.time` will be interpreted as a specific time of day at which the
                  job should run. This could be either today or, if the time has already passed,
                  tomorrow. If the timezone (:attr:`datetime.time.tzinfo`) is :obj:`None`, the
                  default timezone of the bot will be used.

            context (:obj:`object`, optional): Additional data needed for the callback function.
                Can be accessed through :attr:`Job.context` in the callback. Defaults to
                :obj:`None`.
            name (:obj:`str`, optional): The name of the new job. Defaults to
                ``callback.__name__``.
            job_kwargs (:obj:`dict`, optional): Arbitrary keyword arguments to pass to the
                :meth:`apscheduler.schedulers.base.BaseScheduler.add_job()`.

        Returns:
            :class:`telegram.ext.Job`: The new :class:`Job` instance that has been added to the job
            queue.

        """
        if not job_kwargs:
            job_kwargs = {}

        name = name or callback.__name__
        job = Job(callback, context, name)
        date_time = self._parse_time_input(when, shift_day=True)

        j = self.scheduler.add_job(
            job,
            name=name,
            trigger='date',
            run_date=date_time,
            args=(self.dispatcher,),
            timezone=date_time.tzinfo or self.scheduler.timezone,
            **job_kwargs,
        )

        job.job = j
        return job

    def run_repeating(
        self,
        callback: JobCallback,
        interval: Union[float, datetime.timedelta],
        first: Union[float, datetime.timedelta, datetime.datetime, datetime.time] = None,
        last: Union[float, datetime.timedelta, datetime.datetime, datetime.time] = None,
        context: object = None,
        name: str = None,
        job_kwargs: JSONDict = None,
    ) -> 'Job':
        """Creates a new :class:`Job` instance that runs at specified intervals and adds it to the
            queue.

        Note:
            For a note about DST, please see the documentation of `APScheduler`_.

        .. _`APScheduler`: https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html
                           #daylight-saving-time-behavior

        Args:
            callback (:obj:`callable`): The callback function that should be executed by the new
                job. Callback signature: ``def callback(context: CallbackContext)``
            interval (:obj:`int` | :obj:`float` | :obj:`datetime.timedelta`): The interval in which
                the job will run. If it is an :obj:`int` or a :obj:`float`, it will be interpreted
                as seconds.
            first (:obj:`int` | :obj:`float` | :obj:`datetime.timedelta` |                        \
                   :obj:`datetime.datetime` | :obj:`datetime.time`, optional):
                Time in or at which the job should run. This parameter will be interpreted
                depending on its type.

                * :obj:`int` or :obj:`float` will be interpreted as "seconds from now" in which the
                  job should run.
                * :obj:`datetime.timedelta` will be interpreted as "time from now" in which the
                  job should run.
                * :obj:`datetime.datetime` will be interpreted as a specific date and time at
                  which the job should run. If the timezone (:attr:`datetime.datetime.tzinfo`) is
                  :obj:`None`, the default timezone of the bot will be used.
                * :obj:`datetime.time` will be interpreted as a specific time of day at which the
                  job should run. This could be either today or, if the time has already passed,
                  tomorrow. If the timezone (:attr:`datetime.time.tzinfo`) is :obj:`None`, the
                  default timezone of the bot will be used.

                Defaults to ``interval``
            last (:obj:`int` | :obj:`float` | :obj:`datetime.timedelta` |                        \
                   :obj:`datetime.datetime` | :obj:`datetime.time`, optional):
                Latest possible time for the job to run. This parameter will be interpreted
                depending on its type. See ``first`` for details.

                If ``last`` is :obj:`datetime.datetime` or :obj:`datetime.time` type
                and ``last.tzinfo`` is :obj:`None`, the default timezone of the bot will be
                assumed.

                Defaults to :obj:`None`.
            context (:obj:`object`, optional): Additional data needed for the callback function.
                Can be accessed through :attr:`Job.context` in the callback. Defaults to
                :obj:`None`.
            name (:obj:`str`, optional): The name of the new job. Defaults to
                ``callback.__name__``.
            job_kwargs (:obj:`dict`, optional): Arbitrary keyword arguments to pass to the
                :meth:`apscheduler.schedulers.base.BaseScheduler.add_job()`.

        Returns:
            :class:`telegram.ext.Job`: The new :class:`Job` instance that has been added to the job
            queue.

        """
        if not job_kwargs:
            job_kwargs = {}

        name = name or callback.__name__
        job = Job(callback, context, name)

        dt_first = self._parse_time_input(first)
        dt_last = self._parse_time_input(last)

        if dt_last and dt_first and dt_last < dt_first:
            raise ValueError("'last' must not be before 'first'!")

        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()

        j = self.scheduler.add_job(
            job,
            trigger='interval',
            args=(self.dispatcher,),
            start_date=dt_first,
            end_date=dt_last,
            seconds=interval,
            name=name,
            **job_kwargs,
        )

        job.job = j
        return job

    def run_monthly(
        self,
        callback: JobCallback,
        when: datetime.time,
        day: int,
        context: object = None,
        name: str = None,
        job_kwargs: JSONDict = None,
    ) -> 'Job':
        """Creates a new :class:`Job` that runs on a monthly basis and adds it to the queue.

        .. versionchanged:: 14.0
            The ``day_is_strict`` argument was removed. Instead one can now pass -1 to the ``day``
            parameter to have the job run on the last day of the month.

        .. versionchanged:: 14.0
            The ``day_is_strict`` argument was removed. Instead one can now pass -1 to the ``day``
            parameter to have the job run on the last day of the month.

        Args:
            callback (:obj:`callable`):  The callback function that should be executed by the new
                job. Callback signature: ``def callback(context: CallbackContext)``
            when (:obj:`datetime.time`): Time of day at which the job should run. If the timezone
                (``when.tzinfo``) is :obj:`None`, the default timezone of the bot will be used.
            day (:obj:`int`): Defines the day of the month whereby the job would run. It should
                be within the range of 1 and 31, inclusive. If a month has fewer days than this
                number, the job will not run in this month. Passing -1 leads to the job running on
                the last day of the month.
            context (:obj:`object`, optional): Additional data needed for the callback function.
                Can be accessed through :attr:`Job.context` in the callback. Defaults to
                :obj:`None`.
            name (:obj:`str`, optional): The name of the new job. Defaults to
                ``callback.__name__``.
            job_kwargs (:obj:`dict`, optional): Arbitrary keyword arguments to pass to the
                :meth:`apscheduler.schedulers.base.BaseScheduler.add_job()`.

        Returns:
            :class:`telegram.ext.Job`: The new :class:`Job` instance that has been added to the job
            queue.

        """
        if not job_kwargs:
            job_kwargs = {}

        name = name or callback.__name__
        job = Job(callback, context, name)

        j = self.scheduler.add_job(
            job,
            trigger='cron',
            args=(self.dispatcher,),
            name=name,
            day='last' if day == -1 else day,
            hour=when.hour,
            minute=when.minute,
            second=when.second,
            timezone=when.tzinfo or self.scheduler.timezone,
            **job_kwargs,
        )
        job.job = j
        return job

    def run_daily(
        self,
        callback: JobCallback,
        time: datetime.time,
        days: Tuple[int, ...] = tuple(range(7)),
        context: object = None,
        name: str = None,
        job_kwargs: JSONDict = None,
    ) -> 'Job':
        """Creates a new :class:`Job` that runs on a daily basis and adds it to the queue.

        Note:
            For a note about DST, please see the documentation of `APScheduler`_.

        .. _`APScheduler`: https://apscheduler.readthedocs.io/en/stable/modules/triggers/cron.html
                           #daylight-saving-time-behavior

        Args:
            callback (:obj:`callable`): The callback function that should be executed by the new
                job. Callback signature: ``def callback(context: CallbackContext)``
            time (:obj:`datetime.time`): Time of day at which the job should run. If the timezone
                (:obj:`time.tzinfo`) is :obj:`None`, the default timezone of the bot will be used.
            days (Tuple[:obj:`int`], optional): Defines on which days of the week the job should
                run (where ``0-6`` correspond to monday - sunday). Defaults to ``EVERY_DAY``
            context (:obj:`object`, optional): Additional data needed for the callback function.
                Can be accessed through :attr:`Job.context` in the callback. Defaults to
                :obj:`None`.
            name (:obj:`str`, optional): The name of the new job. Defaults to
                ``callback.__name__``.
            job_kwargs (:obj:`dict`, optional): Arbitrary keyword arguments to pass to the
                :meth:`apscheduler.schedulers.base.BaseScheduler.add_job()`.

        Returns:
            :class:`telegram.ext.Job`: The new :class:`Job` instance that has been added to the job
            queue.

        """
        if not job_kwargs:
            job_kwargs = {}

        name = name or callback.__name__
        job = Job(callback, context, name)

        j = self.scheduler.add_job(
            job,
            name=name,
            args=(self.dispatcher,),
            trigger='cron',
            day_of_week=','.join([str(d) for d in days]),
            hour=time.hour,
            minute=time.minute,
            second=time.second,
            timezone=time.tzinfo or self.scheduler.timezone,
            **job_kwargs,
        )

        job.job = j
        return job

    def run_custom(
        self,
        callback: JobCallback,
        job_kwargs: JSONDict,
        context: object = None,
        name: str = None,
    ) -> 'Job':
        """Creates a new custom defined :class:`Job`.

        Args:
            callback (:obj:`callable`): The callback function that should be executed by the new
                job. Callback signature: ``def callback(context: CallbackContext)``
            job_kwargs (:obj:`dict`): Arbitrary keyword arguments. Used as arguments for
                :meth:`apscheduler.schedulers.base.BaseScheduler.add_job`.
            context (:obj:`object`, optional): Additional data needed for the callback function.
                Can be accessed through :attr:`Job.context` in the callback. Defaults to
                :obj:`None`.
            name (:obj:`str`, optional): The name of the new job. Defaults to
                ``callback.__name__``.

        Returns:
            :class:`telegram.ext.Job`: The new :class:`Job` instance that has been added to the job
            queue.

        """
        name = name or callback.__name__
        job = Job(callback, context, name)

        j = self.scheduler.add_job(job, args=(self.dispatcher,), name=name, **job_kwargs)

        job.job = j
        return job

    def start(self) -> None:
        """Starts the job_queue thread."""
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self, wait: bool = True) -> None:
        """Shuts down the :class:`~telegram.ext.JobQueue`.

        Args:
            wait (:obj:`bool`, optional): Whether or not to wait until all currently running jobs
                have finished. Defaults to :obj:`True`.

        """
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)

    def jobs(self) -> Tuple['Job', ...]:
        """Returns a tuple of all *scheduled* jobs that are currently in the :class:`JobQueue`."""
        return tuple(
            Job._from_aps_job(job)  # pylint: disable=protected-access
            for job in self.scheduler.get_jobs()
        )

    def get_jobs_by_name(self, name: str) -> Tuple['Job', ...]:
        """Returns a tuple of all *pending/scheduled* jobs with the given name that are currently
        in the :class:`JobQueue`.
        """
        return tuple(job for job in self.jobs() if job.name == name)


class Job:
    """This class is a convenience wrapper for the jobs held in a :class:`telegram.ext.JobQueue`.
    With the current backend APScheduler, :attr:`job` holds a :class:`apscheduler.job.Job`
    instance.

    Objects of this class are comparable in terms of equality. Two objects of this class are
    considered equal, if their :attr:`id` is equal.

    Note:
        * All attributes and instance methods of :attr:`job` are also directly available as
          attributes/methods of the corresponding :class:`telegram.ext.Job` object.
        * Two instances of :class:`telegram.ext.Job` are considered equal, if their corresponding
          :attr:`job` attributes have the same ``id``.
        * If :attr:`job` isn't passed on initialization, it must be set manually afterwards for
          this :class:`telegram.ext.Job` to be useful.

    .. versionchanged:: 14.0
        Removed argument and attribute :attr:`job_queue`.

    Args:
        callback (:obj:`callable`): The callback function that should be executed by the new job.
            Callback signature: ``def callback(context: CallbackContext)``
        context (:obj:`object`, optional): Additional data needed for the callback function. Can be
            accessed through :attr:`Job.context` in the callback. Defaults to :obj:`None`.
        name (:obj:`str`, optional): The name of the new job. Defaults to ``callback.__name__``.
        job (:class:`apscheduler.job.Job`, optional): The APS Job this job is a wrapper for.

    Attributes:
        callback (:obj:`callable`): The callback function that should be executed by the new job.
        context (:obj:`object`): Optional. Additional data needed for the callback function.
        name (:obj:`str`): Optional. The name of the new job.
        job (:class:`apscheduler.job.Job`): Optional. The APS Job this job is a wrapper for.
    """

    __slots__ = (
        'callback',
        'context',
        'name',
        '_removed',
        '_enabled',
        'job',
    )

    def __init__(
        self,
        callback: JobCallback,
        context: object = None,
        name: str = None,
        job: APSJob = None,
    ):

        self.callback = callback
        self.context = context
        self.name = name or callback.__name__

        self._removed = False
        self._enabled = False

        self.job = cast(APSJob, job)  # skipcq: PTC-W0052

    async def run(self, dispatcher: 'Dispatcher') -> None:
        """Executes the callback function independently of the jobs schedule. Also calls
        :meth:`telegram.ext.Dispatcher.update_persistence`.

        .. versionchanged:: 14.0
            Calls :meth:`telegram.ext.Dispatcher.update_persistence`.

        Args:
            dispatcher (:class:`telegram.ext.Dispatcher`): The dispatcher this job is associated
                with.
        """
        try:
            await run_non_blocking(
                func=self.callback,
                args=(dispatcher.context_types.context.from_job(self, dispatcher),),
            )
        except Exception as exc:
            await dispatcher.dispatch_error(None, exc, job=self)
        finally:
            dispatcher.update_persistence(None)

    async def __call__(self, dispatcher: 'Dispatcher') -> None:
        """Shortcut for::

            await job.run(dispatcher)

        Warning:
            The fact that jobs are callable should be considered an implementation detail and not
            as part of PTBs public API.

        .. versionadded:: 14.0

        Args:
            dispatcher (:class:`telegram.ext.Dispatcher`): The dispatcher this job is associated
                with.
        """
        await self.run(dispatcher=dispatcher)

    def schedule_removal(self) -> None:
        """
        Schedules this job for removal from the :class:`JobQueue`. It will be removed without
        executing its callback function again.
        """
        self.job.remove()
        self._removed = True

    @property
    def removed(self) -> bool:
        """:obj:`bool`: Whether this job is due to be removed."""
        return self._removed

    @property
    def enabled(self) -> bool:
        """:obj:`bool`: Whether this job is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, status: bool) -> None:
        if status:
            self.job.resume()
        else:
            self.job.pause()
        self._enabled = status

    @property
    def next_t(self) -> Optional[datetime.datetime]:
        """
        :class:`datetime.datetime`: Datetime for the next job execution.
        Datetime is localized according to :attr:`datetime.datetime.tzinfo`.
        If job is removed or already ran it equals to :obj:`None`.

        Warning:
            This attribute is only available, if the :class:`telegram.ext.JobQueue` this job
            belongs to is already started. Otherwise APScheduler raises an :exc:`AttributeError`.
        """
        return self.job.next_run_time

    @classmethod
    def _from_aps_job(cls, job: APSJob) -> 'Job':
        return job.func

    def __getattr__(self, item: str) -> object:
        try:
            return getattr(self.job, item)
        except AttributeError as exc:
            raise AttributeError(
                f"Neither 'telegram.ext.Job' nor 'apscheduler.job.Job' has attribute '{item}'"
            ) from exc

    def __lt__(self, other: object) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self.id == other.id
        return False
