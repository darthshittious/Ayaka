"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
import datetime
import difflib
import typing

import discord
from discord.ext import commands, tasks

from utils import cache, formats
from utils.paginator import ListPageSource, RoboPages


if typing.TYPE_CHECKING:
    import asyncpg

    from bot import Ayaka
    from utils.context import Context, GuildContext

    class SnipeContext(GuildContext):
        snipe_conf: SnipeConfig


class RequiresSnipe(commands.CheckFailure):
    """Requires snipe configured."""


class SnipePageSource(ListPageSource):
    def __init__(self, data, embeds):
        self.data = data
        self.embeds = embeds
        super().__init__(data, per_page=1)

    async def format_page(self, menu, entries):
        return self.embeds[entries]


class SnipeConfig:
    __slots__ = ('bot', 'guild_id', 'record', 'channel_ids', 'member_ids')

    def __init__(self, *, guild_id: int, bot: Ayaka, record: asyncpg.Record | None = None):
        self.guild_id = guild_id
        self.bot = bot
        self.record = record

        if record:
            self.channel_ids = record['blacklisted_channels']
            self.member_ids = record['blacklisted_members']
        else:
            self.channel_ids = []
            self.member_ids = []

    @property
    def configured(self):
        guild = self.bot.get_guild(self.guild_id)
        if self.record:
            return guild and self.record


def requires_snipe():
    async def predicate(ctx: SnipeContext) -> bool:
        if ctx.guild is None:
            return False
        cog: Snipe = ctx.bot.get_cog('Snipe')  # type: ignore # ???

        ctx.snipe_conf = await cog.get_snipe_config(ctx.guild.id, connection=ctx.db)
        if ctx.snipe_conf.configured is None:
            raise RequiresSnipe('Sniping is not set up.')
        return True

    return commands.check(predicate)


def can_manage_snipe():
    async def predicate(ctx: GuildContext) -> bool:
        if await ctx.bot.is_owner(ctx.author):
            return True
        if ctx.author.guild_permissions.manage_messages:
            return True
        raise commands.MissingPermissions(['manage_messages'])

    return commands.check(predicate)


class Snipe(commands.Cog, command_attrs=dict(hidden=True)):
    """Snipe."""

    def __init__(self, bot: Ayaka):
        self.bot = bot
        self.snipe_deletes = []
        self.snipe_edits = []
        self._snipe_lock = asyncio.Lock()
        self.snipe_delete_update.start()
        self.snipe_edit_update.start()

    def cog_unload(self) -> None:
        self.snipe_delete_update.stop()
        self.snipe_edit_update.stop()

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        error = getattr(error, 'original', error)
        if isinstance(error, RequiresSnipe):
            await ctx.send(
                "Seems like this guild isn't configured for snipes. It is on an opt-in basis.\nHave a moderator/admin run `snipe setup`."
            )
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send(
                f'You need {formats.human_join(error.missing_permissions, final="and")} permissions to use this command.'
            )
        elif isinstance(error, commands.CommandOnCooldown):
            if await ctx.bot.is_owner(ctx.author):
                await ctx.reinvoke()
                return
            await ctx.send(f'Ha! Snipes are on cooldown for {error.retry_after:.02f}s.')

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        query1 = 'DELETE FROM snipe_deletes WHERE guild_id = $1;'
        query2 = 'DELETE FROM snipe_edits WHERE guild_id = $1;'
        await self.bot.pool.execute(query1, guild.id)
        await self.bot.pool.execute(query2, guild.id)

    @cache.cache()
    async def get_snipe_config(
        self, guild_id: int, *, connection: asyncpg.Connection | asyncpg.Pool | None = None
    ) -> SnipeConfig:
        connection = connection or self.bot.pool
        query = """SELECT * FROM snipe_config WHERE id = $1;"""
        record = await connection.fetchrow(query, guild_id)
        return SnipeConfig(guild_id=guild_id, bot=self.bot, record=record)

    async def _gen_delete_embeds(self, records: list[asyncpg.Record]) -> list[discord.Embed]:
        embeds = []
        for record in records:
            channel = self.bot.get_channel(record['channel_id'])
            try:
                author = self.bot.get_user(record['user_id']) or await self.bot.fetch_user(record['user_id'])
            except discord.HTTPException:
                author = None
            embed = discord.Embed()
            if not author:
                embed.set_author(name='A deleted user...')
            else:
                embed.set_author(name=f'{author}', icon_url=author.display_avatar.url)
            embed.title = f'Deleted from #{channel}'
            embed.description = f'```\n{record["message_content"]}\n```' if record['message_content'] else None
            if record['attachment_urls']:
                embed.set_image(url=record['attachment_urls'][0])
                if len(record['attachment_urls']) > 1:
                    for item in record['attachment_urls'][1:]:
                        embed.add_field(name='Attachment', value=f'[link]({item})')
            fmt = f'Result {records.index(record) + 1}/{len(records)}'
            author_id = getattr(author, 'id', str(record['user_id']))
            embed.set_footer(text=f'{fmt} | Author ID: {author_id}')
            embed.timestamp = datetime.datetime.fromtimestamp(record['delete_time'], tz=datetime.timezone.utc)
            embeds.append(embed)
        return embeds

    async def _gen_edit_embeds(self, records: list[asyncpg.Record]) -> list[discord.Embed]:
        embeds = []
        for record in records:
            channel = self.bot.get_channel(record['channel_id'])
            try:
                author = self.bot.get_user(record['user_id']) or await self.bot.fetch_user(record['user_id'])
            except discord.HTTPException:
                author = None
            jump = record['jump_url']
            embed = discord.Embed()
            if not author:
                embed.set_author(name='A deleted user...')
            else:
                embed.set_author(name=f'{author}', icon_url=author.display_avatar.url)
            embed.title = f'Edited in #{channel}'
            diff_text = self.get_diff(record['before_content'], record['after_content'])
            if len(diff_text) > 2048:
                gh: GitHub = self.bot.cogs['GitHub']  # type: ignore
                url = await gh.create_gist(diff_text, public=False, filename='snipe_edit.diff')
                embed.description = f'Diff is too large, so I put it in a [gist]({url}).'
            else:
                embed.description = formats.to_codeblock(diff_text, language='diff', escape_md=False) if diff_text else None
            fmt = f'Result {records.index(record) + 1}/{len(records)}'
            author_id = getattr(author, 'id', str(record['user_id']))
            embed.set_footer(text=f'{fmt} | Author ID: {author_id}')
            embed.add_field(name='Jump to message', value=f'[Here!]({jump})')
            embed.timestamp = datetime.datetime.fromtimestamp(record['edited_time'], tz=datetime.timezone.utc)
            embeds.append(embed)
        return embeds

    def get_diff(self, before: str, after: str) -> str:
        before_content = f'{before}\n'.splitlines(keepends=True)
        after_content = f'{after}\n'.splitlines(keepends=True)
        return ''.join(difflib.ndiff(before_content, after_content))

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        parent_id = None
        if not message.guild:
            return
        if not message.content and not message.attachments:
            return
        if message.author.id == self.bot.user.id:
            return
        config = await self.get_snipe_config(message.guild.id)
        if not config.configured:
            return
        if message.author.id in config.member_ids:
            return
        if message.channel.id in config.channel_ids:
            return
        if isinstance(message.channel, discord.Thread):
            if message.channel.parent and message.channel.parent.id in config.channel_ids:
                return
            if message.channel.parent:
                parent_id = message.channel.parent.id
        delete_time = discord.utils.utcnow().replace(microsecond=0).timestamp()
        a_id = message.author.id
        g_id = message.guild.id
        c_id = message.channel.id
        m_id = message.id
        m_content = message.clean_content
        attachs = [attachment.proxy_url for attachment in message.attachments]
        async with self._snipe_lock:
            self.snipe_deletes.append(
                {
                    'user_id': a_id,
                    'guild_id': g_id,
                    'channel_id': c_id,
                    'message_id': m_id,
                    'message_content': m_content,
                    'attachment_urls': attachs,
                    'delete_time': int(delete_time),
                    'parent_id': parent_id,
                }
            )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        parent_id = None
        if not before.guild:
            return

        if not before.content:
            return
        if before.content == after.content:
            return
        if before.author.id == self.bot.user.id:
            return
        config = await self.get_snipe_config(before.guild.id)
        if not config.configured:
            return
        if before.author.id in config.member_ids:
            return
        if before.channel.id in config.channel_ids:
            return
        if isinstance(before.channel, discord.Thread):
            if before.channel.parent and before.channel.parent.id in config.channel_ids:
                return
            if before.channel.parent:
                parent_id = before.channel.parent.id
        edited_time = after.edited_at or discord.utils.utcnow()
        edited_time = edited_time.replace(microsecond=0).timestamp()
        a_id = after.author.id
        g_id = before.guild.id
        c_id = after.channel.id
        m_id = after.id
        before_content = before.clean_content
        after_content = after.clean_content
        async with self._snipe_lock:
            self.snipe_edits.append(
                {
                    'user_id': a_id,
                    'guild_id': g_id,
                    'channel_id': c_id,
                    'message_id': m_id,
                    'before_content': before_content,
                    'after_content': after_content,
                    'edited_time': int(edited_time),
                    'jump_url': after.jump_url,
                    'parent_id': parent_id,
                }
            )

    @commands.group(name='snipe', invoke_without_command=True, cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.user)
    @requires_snipe()
    async def show_snipes(
        self,
        ctx: GuildContext,
        amount: int | None = 5,
        channel: discord.TextChannel | discord.Thread = commands.parameter(
            default=lambda x: x.channel, displayed_default='<current>'
        ),
    ):
        """Select the last N snipes from this channel."""

        if channel != ctx.channel:
            if not await can_manage_snipe().predicate(ctx):
                await ctx.send('Sorry, you need to have "Manage Messages" to view another channel.')
                return
            if channel.is_nsfw() and not ctx.channel.is_nsfw():
                await ctx.send('No peeping NSFW stuff in here you detty pig.')
                return
        query = 'SELECT * FROM snipe_deletes WHERE guild_id = $2 AND channel_id = $3 ORDER BY id DESC LIMIT $1;'
        results = await ctx.db.fetch(query, amount, ctx.guild.id, channel.id)
        dict_results = [dict(result) for result in results]
        local_snipes = [snipe for snipe in self.snipe_deletes if snipe['channel_id'] == channel.id]
        full_results = dict_results + local_snipes
        if not full_results:
            await ctx.send('No snipes for this channel.')
            return
        full_results = sorted(full_results, key=lambda d: d['delete_time'], reverse=True)[:amount]
        embeds = await self._gen_delete_embeds(full_results)
        pages = RoboPages(SnipePageSource(range(0, len(embeds)), embeds), ctx=ctx)
        await pages.start()

    @show_snipes.command(name='setup')
    @can_manage_snipe()
    @commands.guild_only()
    async def setup_snipe(self, ctx: GuildContext):
        """Opts in to snipe. Requires Manage Messages."""

        self.get_snipe_config.invalidate(self, ctx.guild.id)
        config = await self.get_snipe_config(ctx.guild.id, connection=ctx.db)
        query = """INSERT INTO snipe_config (id, blacklisted_channels, blacklisted_members)
                   VALUES ($1, $2, $3)
                """
        if not config.record:
            await ctx.db.execute(query, ctx.guild.id, [], [])
            await ctx.message.add_reaction(ctx.tick(True))
        else:
            await ctx.send("You're already opted in to snipe. Did you mean to disable it?")
        self.get_snipe_config.invalidate(self, ctx.guild.id)

    @show_snipes.command(name='destroy', aliases=['desetup'])
    @can_manage_snipe()
    @requires_snipe()
    async def snipe_desetup(self, ctx: SnipeContext):
        """Remove the ability to snipe here."""

        config = ctx.snipe_conf
        if not config.configured:
            await ctx.send('Sniping is not enabled for this guild.')
            return
        confirm = await ctx.prompt('This will delete all data stored from this guild from my snipes. Are you sure?')
        if not confirm:
            await ctx.message.add_reaction(ctx.tick(False))
        queries = [f'DELETE FROM snipe_{x} WHERE id = $1;' for x in ('config', 'deletes', 'edits')]
        for query in queries:
            await self.bot.pool.execute(query, ctx.guild.id)
        self.get_snipe_config.invalidate(self, ctx.guild.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @show_snipes.command(name='optout', aliases=['out', 'disable'])
    @requires_snipe()
    @commands.guild_only()
    async def snipe_optout(
        self,
        ctx: SnipeContext,
        *,
        entity: discord.Member | discord.TextChannel = commands.parameter(
            default=lambda x: x.author, displayed_default='<self>'
        ),
    ):
        """Let's you toggle it for this channel / member / self."""

        config = ctx.snipe_conf
        if isinstance(entity, discord.TextChannel) or entity != ctx.author:
            if not await can_manage_snipe().predicate(ctx):
                raise commands.MissingPermissions(['manage_messages'])
        if entity.id in config.channel_ids or entity.id in config.member_ids:
            await ctx.send(
                f'{entity.mention} is already opted out of sniping.', allowed_mentions=discord.AllowedMentions.none()
            )
            return
        query = """UPDATE snipe_config SET
                   blacklisted_{0} = blacklisted_{0} || $2
                   WHERE id = $1;
                """
        if isinstance(entity, discord.Member):
            query = query.format('members')
        elif isinstance(entity, discord.TextChannel):
            query = query.format('channels')
        await self.bot.pool.execute(query, ctx.guild.id, [entity.id])
        self.get_snipe_config.invalidate(self, ctx.guild.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @show_snipes.command(name='optin', aliases=['in', 'enable'], usage='[member|channel] (defaults to self.)')
    @requires_snipe()
    async def snipe_optin(
        self,
        ctx: SnipeContext,
        entity: discord.Member | discord.TextChannel = commands.parameter(
            default=lambda x: x.author, displayed_default='<self>'
        ),
    ):
        """Let's you toggle it for this channel / member / self."""
        config = ctx.snipe_conf
        if isinstance(entity, discord.TextChannel) or entity != ctx.author:
            if not await can_manage_snipe().predicate(ctx):
                raise commands.MissingPermissions(['manage_messages'])
        if entity.id in config.channel_ids or entity.id in config.member_ids:
            await ctx.send(
                f'{entity.mention} is currently not opted out of sniping.', allowed_mentions=discord.AllowedMentions.none()
            )
            return
        query = """UPDATE snipe_config SET
                   blacklisted_{0} = array_remove(blacklisted_{0}, $2)
                   WHERE id = $1;
                """
        if isinstance(entity, discord.Member):
            query = query.format('members')
        elif isinstance(entity, discord.TextChannel):
            query = query.format('channels')
        await self.bot.pool.execute(query, ctx.guild.id, entity.id)
        self.get_snipe_config.invalidate(self, ctx.guild.id)
        await ctx.message.add_reaction(ctx.tick(True))

    @show_snipes.command(name='edits', aliases=['edit', 'e'], cooldown_after_parsing=True)
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.user)
    @requires_snipe()
    async def show_edit_snipes(
        self,
        ctx: GuildContext,
        amount: int | None = 5,
        channel: discord.TextChannel | discord.Thread = commands.parameter(
            default=lambda x: x.channel, displayed_default='<current>'
        ),
    ):
        """Edit snipes. Shows the last N. Must have manage_messages to view from another channel."""
        assert amount is not None

        if channel != ctx.channel:
            if not await can_manage_snipe().predicate(ctx):
                await ctx.send('Sorry, you need to have "Manage Messages" to view another channel.')
                return
        if not 0 < amount < 15:
            raise commands.BadArgument('No more than 15 indexes at once.')

        query = 'SELECT * FROM snipe_edits WHERE guild_id = $2 AND channel_id = $3 ORDER BY id DESC LIMIT $1;'
        results = await self.bot.pool.fetch(query, amount, ctx.guild.id, channel.id)
        dict_results = [dict(result) for result in results]
        local_snipes = [snipe for snipe in self.snipe_edits if snipe['channel_id'] == channel.id]
        full_results = dict_results + local_snipes
        full_results = sorted(full_results, key=lambda d: d['edited_time'], reverse=True)[:amount]
        embeds = await self._gen_edit_embeds(full_results)
        if not embeds:
            await ctx.send('No edit snipes for this channel.')
            return
        pages = RoboPages(SnipePageSource(range(0, len(embeds)), embeds), ctx=ctx)
        await pages.start()

    @show_snipes.command(name='clear', aliases=['remove', 'delete'], hidden=True)
    @requires_snipe()
    async def _snipe_clear(
        self,
        ctx: GuildContext,
        target: discord.Member | discord.TextChannel = commands.parameter(
            default=lambda x: x.author, displayed_default='<self>'
        ),
    ):
        """Remove all data stored on snipes, including edits for the target Member or TextChannel.

        You must have the Manage Messages permission to specify a non-self target.
        """

        member = False
        channel = False
        if target != ctx.author:
            if not await can_manage_snipe().predicate(ctx):
                raise commands.MissingPermissions(['manage_messages'])
        queries = """
                  DELETE FROM snipe_deletes WHERE guild_id = $1 AND {0}_id = $2 {1};
                  DELETE FROM snipe_edits WHERE guild_id = $1 AND {0}_id = $2 {1};
                  """
        if isinstance(target, discord.TextChannel):
            queries = queries.format('channel', 'OR parent_id = $2')
            channel = True
        elif isinstance(target, discord.Member):
            queries = queries.format('user', '')
            member = True
        else:
            # unreachable
            return
        confirm = await ctx.prompt('This is a destructive action and non-recoverable. Are you sure?')
        if not confirm:
            return
        await self.bot.pool.execute(queries, ctx.guild.id, target.id)
        for item in self.snipe_deletes:
            if member:
                if item['user_id'] == target.id:
                    self.snipe_deletes.remove(item)
            elif channel:
                if item['channel_id'] == target.id or item['parent_id'] == target.id:
                    self.snipe_deletes.remove(item)
        return await ctx.message.add_reaction(ctx.tick(True))

    @tasks.loop(minutes=1)
    async def snipe_delete_update(self):
        query = """INSERT INTO snipe_deletes (user_id, guild_id, channel_id, parent_id, message_id, message_content, attachment_urls, delete_time)
                   SELECT x.user_id, x.guild_id, x.channel_id, x.parent_id, x.message_id, x.message_content, x.attachment_urls, x.delete_time
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(user_id BIGINT, guild_id BIGINT, channel_id BIGINT, parent_id BIGINT, message_id BIGINT, message_content TEXT, attachment_urls TEXT[], delete_time BIGINT)
                """
        async with self._snipe_lock:
            await self.bot.pool.execute(query, self.snipe_deletes)
            self.snipe_deletes.clear()

    @tasks.loop(minutes=1)
    async def snipe_edit_update(self):
        query = """INSERT INTO snipe_edits (user_id, guild_id, channel_id, parent_id, message_id, before_content, after_content, edited_time, jump_url)
                   SELECT x.user_id, x.guild_id, x.channel_id, x.parent_id, x.message_id, x.before_content, x.after_content, x.edited_time, x.jump_url
                   FROM jsonb_to_recordset($1::jsonb) AS
                   x(user_id BIGINT, guild_id BIGINT, channel_id BIGINT, parent_id BIGINT, message_id BIGINT, before_content TEXT, after_content TEXT, edited_time BIGINT, jump_url TEXT)
                """
        async with self._snipe_lock:
            await self.bot.pool.execute(query, self.snipe_edits)
            self.snipe_edits.clear()


async def setup(bot: Ayaka):
    await bot.add_cog(Snipe(bot))
