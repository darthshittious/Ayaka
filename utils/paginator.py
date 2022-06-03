"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

import asyncio
from textwrap import shorten
from typing import TYPE_CHECKING, Any, Generic, Optional, Type, TypeVar, overload

import discord
from discord.ext import menus
from discord.ext.commands import Paginator as CommandPaginator

from utils.context import Context


if TYPE_CHECKING:
    import mangadex
    from typing_extensions import Self

T = TypeVar('T')
SourceT = TypeVar('SourceT', bound='menus.PageSource')


class RoboPages(discord.ui.View, Generic[SourceT]):
    def __init__(self, source: SourceT, *, ctx: Context, check_embeds: bool = True, compact: bool = False):
        super().__init__()
        self.source: SourceT = source
        self.check_embeds: bool = check_embeds
        self.ctx: Context = ctx
        self.message: Optional[discord.Message] = None
        self.current_page: int = 0
        self.compact: bool = compact
        self.input_lock = asyncio.Lock()
        self.clear_items()
        self.fill_items()

    def fill_items(self) -> None:
        if not self.compact:
            self.numbered_page.row = 1
            self.stop_pages.row = 1

        if self.source.is_paginating():
            max_pages = self.source.get_max_pages()
            use_last_and_first = max_pages is not None and max_pages >= 2
            if use_last_and_first:
                self.add_item(self.go_to_first_page)
            self.add_item(self.go_to_previous_page)
            if not self.compact:
                self.add_item(self.go_to_current_page)
            self.add_item(self.go_to_next_page)
            if use_last_and_first:
                self.add_item(self.go_to_last_page)
            if not self.compact:
                self.add_item(self.numbered_page)
            self.add_item(self.stop_pages)

    async def _get_kwargs_from_page(self, page: int) -> dict[str, Any]:
        value = await discord.utils.maybe_coroutine(self.source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {'content': value, 'embed': None}
        elif isinstance(value, discord.Embed):
            return {'embed': value, 'content': None}
        else:
            return {}

    async def show_page(self, interaction: discord.Interaction, page_number: int) -> None:
        page = await self.source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        self._update_labels(page_number)
        if kwargs:
            if interaction.response.is_done():
                if self.message:
                    await self.message.edit(**kwargs, view=self)
            else:
                await interaction.response.edit_message(**kwargs, view=self)

    def _update_labels(self, page_number: int) -> None:
        self.go_to_first_page.disabled = page_number == 0
        if self.compact:
            max_pages = self.source.get_max_pages()
            self.go_to_last_page.disabled = max_pages is None or (page_number + 1) >= max_pages
            self.go_to_next_page.disabled = max_pages is not None and (page_number + 1) >= max_pages
            self.go_to_previous_page.disabled = page_number == 0
            return

        self.go_to_current_page.label = str(page_number + 1)
        self.go_to_previous_page.label = str(page_number)
        self.go_to_next_page.label = str(page_number + 2)
        self.go_to_next_page.disabled = False
        self.go_to_previous_page.disabled = False
        self.go_to_first_page.disabled = False

        max_pages = self.source.get_max_pages()
        if max_pages is not None:
            self.go_to_last_page.disabled = (page_number + 1) >= max_pages
            if (page_number + 1) >= max_pages:
                self.go_to_next_page.disabled = True
                self.go_to_next_page.label = '…'
            if page_number == 0:
                self.go_to_previous_page.disabled = True
                self.go_to_previous_page.label = '…'

    async def show_checked_page(self, interaction: discord.Interaction, page_number: int) -> None:
        max_pages = self.source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(interaction, page_number)
            elif max_pages > page_number >= 0:
                await self.show_page(interaction, page_number)
        except IndexError:
            # an error happened that can't be handled, so ignore it
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id in (self.ctx.bot.owner_id, self.ctx.author.id):
            return True
        await interaction.response.send_message('This pagination menu cannot be controlled by you, sorry!', ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.edit(view=None)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        if interaction.response.is_done():
            await interaction.followup.send('An unknown error occurred, sorry', ephemeral=True)
        else:
            await interaction.response.send_message('An unknown error occurred, sorry', ephemeral=True)

    async def start(self, *, content: str | None = None) -> None:
        if self.check_embeds and not self.ctx.channel.permissions_for(self.ctx.me).embed_links:  # type: ignore
            await self.ctx.send('Bot does not have embed links permission in this channel.')
            return

        await self.source._prepare_once()
        page = await self.source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        if content:
            kwargs.setdefault('content', content)
        self._update_labels(0)
        self.message = await self.ctx.send(**kwargs, view=self)

    @discord.ui.button(label='≪', style=discord.ButtonStyle.grey)
    async def go_to_first_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Go to the first page."""
        await self.show_page(interaction, 0)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.blurple)
    async def go_to_previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Go to the previous page."""
        await self.show_checked_page(interaction, self.current_page - 1)

    @discord.ui.button(label='Current', style=discord.ButtonStyle.grey, disabled=True)
    async def go_to_current_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label='Next', style=discord.ButtonStyle.blurple)
    async def go_to_next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Go to the next page."""
        await self.show_checked_page(interaction, self.current_page + 1)

    @discord.ui.button(label='≫', style=discord.ButtonStyle.grey)
    async def go_to_last_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Go to the last page."""
        # The call here is safe because it's guarded by skip_if
        await self.show_page(interaction, self.source.get_max_pages() - 1)  # type: ignore

    class SkipToPageModal(discord.ui.Modal):
        page_number = discord.ui.TextInput(label='Page number', style=discord.TextStyle.short, required=True, min_length=1)

        def __init__(self, menu: RoboPages) -> None:
            super().__init__(title='Skip to page', timeout=menu.timeout)
            self.menu = menu
            self.page_number.label = f'Page number (1-{menu.source.get_max_pages()})'
            self.page_number.min_length = 1
            self.page_number.max_length = len(str(menu.source.get_max_pages()))

        async def on_submit(self, interaction: discord.Interaction) -> None:
            try:
                page = int(self.page_number.value or '') - 1
                if not 0 <= page < self.menu.source.get_max_pages():
                    raise ValueError()
            except ValueError:
                await interaction.response.send_message(
                    f'``{self.page_number.value}`` could not be converted to a page number', ephemeral=True
                )
            else:
                await self.menu.show_checked_page(interaction, page)

    @discord.ui.button(label='Skip to page...', style=discord.ButtonStyle.grey)
    async def numbered_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Lets you type a page number to go to."""
        await interaction.response.send_modal(self.SkipToPageModal(self))

    @discord.ui.button(label='Quit', style=discord.ButtonStyle.red)
    async def stop_pages(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Stops the pagination session."""
        await interaction.response.defer()
        await interaction.delete_original_message()
        self.stop()


class FieldPageSource(menus.ListPageSource, Generic[T]):
    def __init__(self, entries, *, per_page=12):
        super().__init__(entries, per_page=per_page)
        self.embed = discord.Embed(colour=discord.Colour.blurple())

    async def format_page(self, menu, entries):
        self.embed.clear_fields()
        self.embed.description = None

        for key, value in entries:
            self.embed.add_field(name=key, value=value, inline=False)

        maximum = self.get_max_pages()
        if maximum > 1:
            text = f'Page {menu.current_page + 1}/{maximum} ({len(self.entries)} entries)'
            self.embed.set_footer(text=text)
        return self.embed


class TextPageSource(menus.ListPageSource):
    def __init__(self, text, *, prefix='```', suffix='```', max_size=2000):
        pages = CommandPaginator(prefix=prefix, suffix=suffix, max_size=max_size - 200)
        for line in text.split('\n'):
            pages.add_line(line)

        super().__init__(entries=pages.pages, per_page=1)

    async def format_page(self, menu, content):
        maximum = self.get_max_pages()
        if maximum > 1:
            return f'{content}\nPage {menu.current_page + 1}/{maximum}'
        return content


class SimplePageSource(menus.ListPageSource):
    async def format_page(self, menu, entries):
        pages = []
        for index, entry in enumerate(entries, start=menu.current_page * self.per_page):
            pages.append(f'{index + 1}. {entry}')

        maximum = self.get_max_pages()
        if maximum > 1:
            footer = f'Page {menu.current_page + 1}/{maximum} ({len(self.entries)} entries)'
            menu.embed.set_footer(text=footer)

        menu.embed.description = '\n'.join(pages)
        return menu.embed


class SimplePages(RoboPages):
    def __init__(self, entries: list[Any], *, ctx: Context, per_page: int = 12) -> None:
        super().__init__(SimplePageSource(entries, per_page=per_page), ctx=ctx)
        self.embed = discord.Embed(colour=discord.Colour.blurple())


class SimpleListSource(menus.ListPageSource, Generic[T]):
    def __init__(self, data: list[T], per_page: int = 1):
        self.data = data
        super().__init__(data, per_page=per_page)

    @overload
    async def format_page(self, _: menus.Menu, entries: list[T]) -> list[T]:
        ...

    @overload
    async def format_page(self, _: menus.Menu, entries: T) -> T:
        ...

    async def format_page(self, _: menus.Menu, entries: T | list[T]):
        return entries


class MangadexEmbed(discord.Embed):
    @classmethod
    async def from_chapter(cls: Type[Self], chapter: mangadex.Chapter, *, nsfw_allowed: bool = False) -> Self:
        assert chapter.manga is not None
        parent = chapter.manga
        parent_title = parent.alt_titles.get('ja-ro', parent.title)
        if chapter.title:
            parent_title += f' - {chapter.title}'
        if chapter.chapter:
            parent_title += f' [Chapter {chapter.chapter}]'
        if parent.cover_url() is None:
            await parent.get_cover()

        self = cls(title=parent_title, colour=discord.Colour.red(), url=chapter.url)
        self.set_footer(text=chapter.id)
        self.timestamp = chapter.created_at
        self.add_field(name='Manga link is:', value=f'[here!]({parent.url})')
        if parent.content_rating is mangadex.ContentRating.safe or (nsfw_allowed is True):
            if chapter.manga.cover_url() is None:
                await chapter.manga.get_cover()
            self.set_thumbnail(url=chapter.manga.cover_url())
        return self

    @classmethod
    async def from_manga(cls: Type[Self], manga: mangadex.Manga, *, nsfw_allowed: bool = False) -> Self:
        self = cls(title=manga.title, colour=discord.Colour.blue(), url=manga.url)
        if manga.description:
            self.description = shorten(manga.description, width=2000)
        if manga.tags:
            self.add_field(name='Tags:', value=', '.join([tag.name for tag in manga.tags]), inline=False)
        if manga.publication_demographic:
            self.add_field(name='Publication Demographic:', value=str(manga.publication_demographic).title())
        if manga.content_rating:
            self.add_field(name='Content Rating:', value=str(manga.content_rating).title(), inline=False)
        if manga.artists:
            self.add_field(name='Attributed Artists:', value=', '.join([artist.name for artist in manga.artists]))
        if manga.authors:
            self.add_field(name='Attributed Authors:', value=', '.join([author.name for author in manga.authors]))
        if manga.status:
            self.add_field(name='Publication Status:', value=str(manga.status).title(), inline=False)
            if manga.status is mangadex.MangaStatus.completed:
                self.add_field(name='Last Volume:', value=manga.last_volume)
                self.add_field(name='Last Chapter:', value=manga.last_chapter)
        self.set_footer(text=manga.id)
        if manga.content_rating is mangadex.ContentRating.safe or (nsfw_allowed is True):
            if manga.cover_url() is None:
                await manga.get_cover()
            self.set_thumbnail(url=manga.cover_url())
        return self
