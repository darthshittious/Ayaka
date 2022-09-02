"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands
from discord import app_commands
from . import battleship, gobblet

import discord

if TYPE_CHECKING:
    from bot import Ayaka
    from utils.context import GuildContext


class Minigame(commands.GroupCog):
    """Simple minigames to play with others"""

    def __init__(self, bot: Ayaka):
        self.bot = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{VIDEO GAME}', id=None)

    def __repr__(self) -> str:
        return '<cogs.Minigame>'

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.rename(other='with')
    @app_commands.describe(other='The opponent to play with')
    async def gobblet(self, ctx: GuildContext, *, other: discord.Member):
        """Play a game of Gobblet Gobblers"""
        prompt = gobblet.Prompt(ctx.author, other)
        msg = await ctx.send(
            f'{other.mention} has been challenged to a game of Gobblet Gobblers by {ctx.author.mention}.\n'
            'This is a game similar to Tic-Tac-Toe except each piece has an associated strength with it. '
            "A higher strength value eats a piece even if it's already on the board. "
            'Careful, you only have 1 piece of each strength value!\n\n'
            f'Do you accept this challenge, {other.mention}?',
            view=prompt,
        )

        await prompt.wait()
        await msg.delete(delay=10)

    @commands.hybrid_command()
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.rename(other='with')
    @app_commands.describe(other='The opponent to play with')
    async def battleship(self, ctx: GuildContext, *, other: discord.Member):
        """Play a game of battleship with someone else"""
        prompt = battleship.Prompt(ctx.author, other)
        prompt.message = await ctx.send(
            f'{other.mention} has been challenged to a game of Battleship by {ctx.author.mention}.\n'
            f'In order to accept, please press your button below to ready up.',
            view=prompt,
        )


async def setup(bot: Ayaka):
    await bot.add_cog(Minigame(bot))