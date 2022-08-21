"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import datetime
import re
import zoneinfo

import discord
import parsedatetime as pdt
from dateutil.relativedelta import relativedelta
from discord import app_commands
from discord.ext import commands

from bot import Ayaka
from utils.context import Context

from .context import Context
from .formats import format_dt, human_join, plural


# Monkeypatch mins and secs into units
units = pdt.pdtLocales['en_US'].units
units['minutes'].append('mins')
units['seconds'].append('secs')
units['hours'].append('hr')
units['hours'].append('hrs')


class ShortTime:
    compiled = re.compile(
        r"""(?:(?P<years>[0-9])(?:years?|y))?           # e.g. 2y
            (?:(?P<months>[0-9]{1,2})(?:months?|mo))?   # e.g. 2months
            (?:(?P<weeks>[0-9]{1,4})(?:weeks?|w))?      # e.g. 10w
            (?:(?P<days>[0-9]{1,5})(?:days?|d))?        # e.g. 14d
            (?:(?P<hours>[0-9]{1,5})(?:hours?|h))?      # e.g. 12h
            (?:(?P<minutes>[0-9]{1,5})(?:minutes?|m))?  # e.g. 10m
            (?:(?P<seconds>[0-9]{1,5})(?:seconds?|s))?  # e.g. 15s
        """,
        re.VERBOSE,
    )

    def __init__(self, argument: str, *, now: datetime.datetime | None = None):
        match = self.compiled.fullmatch(argument)
        if match is None or not match.group(0):
            raise commands.BadArgument('invalid time provided')

        data = {k: int(v) for k, v in match.groupdict(default=0).items()}
        now = now or datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        self.dt = now + relativedelta(**data)

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> ShortTime:
        return cls(argument, now=ctx.message.created_at)


class HumanTime:
    dt: datetime.datetime
    calendar = pdt.Calendar(version=pdt.VERSION_CONTEXT_STYLE)

    def __init__(self, argument: str, *, now: datetime.datetime | None = None, tz = zoneinfo.ZoneInfo('UTC')):
        now = now or discord.utils.utcnow()
        dt, status = self.calendar.parseDT(argument, sourceTime=now, tzinfo=tz)
        if not status.hasDateOrTime:
            raise commands.BadArgument('invalid time provided, try e.g. "tomorrow" or "3 days"')

        if not status.hasTime:
            # replace it with the current time
            dt = dt.replace(
                hour=now.hour,
                minute=now.minute,
                second=now.second,
                microsecond=now.microsecond,
                tzinfo=datetime.timezone.utc,
            )
        self.dt = dt
        self._past = dt < now

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> HumanTime:
        return cls(argument, now=ctx.message.created_at)


class Time(HumanTime):
    def __init__(self, argument: str, *, now: datetime.datetime | None = None):
        try:
            o = ShortTime(argument, now=now)
        except Exception:
            super().__init__(argument)
        else:
            self.dt = o.dt
            self._past = False


class FutureTime(Time):
    def __init__(self, argument: str, *, now: datetime.datetime | None = None):
        super().__init__(argument, now=now)
        if self._past:
            raise commands.BadArgument('this time is in the past')


class BadTimeTransform(app_commands.AppCommandError):
    pass


class TimeTransformer(app_commands.Transformer):
    @staticmethod
    async def get_timezone(interaction: discord.Interaction) -> zoneinfo.ZoneInfo:
        assert isinstance(interaction.client, Ayaka)
        if interaction.guild is None:
            tz = zoneinfo.ZoneInfo('UTC')
        else:
            row: str | None = await interaction.client.pool.fetchval(
                'SELECT tz FROM tz_store WHERE user_id = $1 and $2 = ANY(guild_ids);',
                interaction.user.id,
                interaction.guild.id,
            )  # type: ignore  # asyncpg woes
            if row:
                tz = zoneinfo.ZoneInfo(row)
            else:
                tz = zoneinfo.ZoneInfo('UTC')
        return tz

    @classmethod
    def transform(cls, interaction: discord.Interaction, value: str) -> datetime.datetime:
        now = interaction.created_at
        try:
            short = ShortTime(value, now=now)
        except commands.BadArgument:
            try:
                human = FutureTime(value, now=now)
            except commands.BadArgument as e:
                raise BadTimeTransform(str(e)) from None
            else:
                return human.dt
        else:
            return short.dt


class FriendlyTimeResult:
    dt: datetime.datetime
    arg: str

    __slots__ = ('dt', 'arg')

    def __init__(self, dt: datetime.datetime) -> None:
        self.dt = dt
        self.arg = ''

    async def ensure_constraints(self, ctx: Context, uft: UserFriendlyTime, now: datetime.datetime, remaining: str) -> None:
        if self.dt < now:
            raise commands.BadArgument('This time is in the past.')
        if not remaining:
            if uft.default is None:
                raise commands.BadArgument('Missing argument after the time.')
            remaining = uft.default
        if uft.converter is not None:
            self.arg = await uft.converter.convert(ctx, remaining)
        else:
            self.arg = remaining


class UserFriendlyTime(commands.Converter):
    dt: datetime.datetime

    def __init__(self, converter: commands.Converter | None = None, *, default: str | None = None):
        if isinstance(converter, type) and issubclass(converter, commands.Converter):
            converter = converter()

        if converter is not None and not isinstance(converter, commands.Converter):
            raise TypeError('commands.Converter subclass necessary.')

        self.converter = converter
        self.default = default

    async def convert(self, ctx: Context, argument: str) -> FriendlyTimeResult:
        try:
            calendar = HumanTime.calendar
            regex = ShortTime.compiled
            now = ctx.message.created_at

            match = regex.match(argument)
            if match is not None and match.group(0):
                data = {k: int(v) for k, v in match.groupdict(default=0).items()}
                remaining = argument[match.end() :].strip()
                result = FriendlyTimeResult(now + relativedelta(**data))
                await result.ensure_constraints(ctx, self, now, remaining)
                return result

            # apparently nlp does not like 'from now'
            # it likes 'from x' in other cases though so let me handle the 'now' case
            if argument.endswith('from now'):
                argument = argument[:-8].strip()

            if argument[0:2] == 'me':
                # starts with 'me to', 'me in', or 'me at '
                if argument[0:6] in ('me to ', 'me in ', 'me at '):
                    argument = argument[6:]

            elements = calendar.nlp(argument, sourceTime=now)
            if elements is None or len(elements) == 0:
                raise commands.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

            # handle the following cases:
            # 'date time' foo
            # date time foo
            # foo date time

            # first the first two cases:
            dt, status, begin, end, _ = elements[0]

            if not status.hasDateOrTime:
                raise commands.BadArgument('Invalid time provided, try e.g. "tomorrow" or "3 days".')

            if begin not in (0, 1) and end != len(argument):
                raise commands.BadArgument(
                    'Time is either in an inappropriate location, which '
                    'must be either at the end or beginning of your input, '
                    'or I just flat out did not understand what you meant. Sorry.'
                )

            if not status.hasTime:
                # replace it with the current time
                dt = dt.replace(
                    hour=now.hour,
                    minute=now.minute,
                    second=now.second,
                    microsecond=now.microsecond,
                )

            # if midnight is provided, just default to next day
            if status.accuracy == pdt.pdtContext.ACU_HALFDAY:
                dt = dt.replace(day=now.day + 1)

            result = FriendlyTimeResult(dt.replace(tzinfo=datetime.timezone.utc))
            remaining = ''

            if begin in (0, 1):
                if begin == 1:
                    # check if it's quoted:
                    if argument[0] != '"':
                        raise commands.BadArgument('Expected quote before time input...')

                    if not (end < len(argument) and argument[end] == '"'):
                        raise commands.BadArgument('If the time is quoted, you must unquote it.')

                    remaining = argument[end + 1 :].lstrip(' ,.!')
                else:
                    remaining = argument[end:].lstrip(' ,.!')
            elif len(argument) == end:
                remaining = argument[:begin].strip()

            await result.ensure_constraints(ctx, self, now, remaining)
            return result
        except Exception:
            import traceback

            traceback.print_exc()
            raise


def human_timedelta(
    dt: datetime.datetime,
    *,
    source: datetime.datetime | None = None,
    accuracy: int | None = 3,
    brief: bool = False,
    suffix: bool = True,
) -> str:
    now = source or (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc))
    # Microsecond free zone
    now = now.replace(microsecond=0)
    dt = dt.replace(microsecond=0)

    # This implementation uses relativedelta instead of the much more obvious
    # divmod approach with seconds because the seconds approach is not entirely
    # accurate once you go over 1 week in terms of accuracy since you have to
    # hardcode a month as 30 or 31 days.
    # A query like '11 months' can be interpreted as '!1 months and 6 days'
    if dt > now:
        delta = relativedelta(dt, now)
        _suffix = ''
    else:
        delta = relativedelta(now, dt)
        _suffix = ' ago' if suffix else ''

    attrs: list[tuple[str, str]] = [
        ('year', 'y'),
        ('month', 'mo'),
        ('day', 'd'),
        ('hour', 'h'),
        ('minute', 'm'),
        ('second', 's'),
    ]

    output = []
    for attr, brief_attr in attrs:
        elem = getattr(delta, attr + 's')
        if not elem:
            continue

        if attr == 'day':
            weeks = delta.weeks
            if weeks:
                elem -= weeks * 7
                if not brief:
                    output.append(format(plural(weeks), 'week'))
                else:
                    output.append(f'{weeks}w')

        if elem <= 0:
            continue

        if brief:
            output.append(f'{elem}{brief_attr}')
        else:
            output.append(format(plural(elem), attr))

    if accuracy is not None:
        output = output[:accuracy]

    if len(output) == 0:
        return 'now'
    else:
        if not brief:
            return human_join(output, final='and') + _suffix
        else:
            return ' '.join(output) + _suffix


def hf_time(dt: datetime.datetime) -> str:
    date_modif = ordinal(dt.day)
    return dt.strftime(f'%A {date_modif} of %B %Y @ %H:%M %Z (%z)')


def ordinal(number: int) -> str:
    return f'{number}{"tsnrhtdd"[(number//10%10!=1)*(number%10<4)*number%10::4]}'


def format_relative(dt: datetime.datetime) -> str:
    return format_dt(dt, 'R')
