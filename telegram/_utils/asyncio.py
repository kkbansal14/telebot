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
"""This module contains helper functions related to usage of the :mod:`asyncio` module.

.. versionadded:: 14.0

Warning:
    Contents of this module are intended to be used internally by the library and *not* by the
    user. Changes to this module are not considered breaking changes and may not be documented in
    the changelog.
"""
import asyncio
import functools
import sys
from functools import partial
from typing import TypeVar, Callable, Sequence, Dict, Any, Union, Coroutine, cast

if sys.version_info >= (3, 8):
    _MANUAL_UNWRAP = False
else:
    _MANUAL_UNWRAP = True


_RT = TypeVar('_RT')


# TODO Remove this once we drop Py3.7
def is_coroutine_function(obj: object) -> bool:
    """Thin wrapper around :func:`asyncio.iscoroutinefunction` that handles the support for
    :func:`functools.partial` which :func:`asyncio.iscoroutinefunction` only natively supports in
    Python 3.8+. Also handles callable classes, where ``__call__`` is async.

    Note:
        Can be removed once support for Python 3.7 is dropped.

    Args:
        obj (:class:`object`): The object to test.

    Returns:
        :obj:`bool`: Whether or not the object is a
            `coroutine function <https://docs.python.org/3/library/asyncio-task.html#coroutine>`

    """
    if not callable(obj):
        return False

    if _MANUAL_UNWRAP:
        while isinstance(obj, partial):
            obj = obj.func  # type: ignore[attr-defined]

    if not callable(obj):
        return False

    return asyncio.iscoroutinefunction(obj) or (
        hasattr(obj, "__call__") and asyncio.iscoroutinefunction(obj.__call__)
    )


async def run_non_blocking(
    func: Callable[..., Union[_RT, Coroutine[Any, Any, _RT]]],
    args: Sequence[object] = None,
    kwargs: Dict[str, object] = None,
) -> _RT:
    """Runs ``func`` such that it doesn't block. This

    * for ``async`` functions the result is ``await`` ed
    * synchronous functions are run in the current event loops default executor - a thread pool

    Args:
        func: The function
        args: Optional. positional arguments as sequence
        kwargs: Optional. keyword arguments as dict
    """
    effective_args = args or ()
    effective_kwargs = kwargs or {}

    if not is_coroutine_function(func):
        func = cast(Callable[..., _RT], func)
        return await asyncio.get_event_loop().run_in_executor(
            executor=None, func=functools.partial(func, *effective_args, **effective_kwargs)
        )

    func = cast(Callable[..., Coroutine[Any, Any, _RT]], func)
    return await func(*effective_args, **effective_kwargs)
