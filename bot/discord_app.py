"""Discord front end.

* @mention the bot in a channel -> it opens a thread and answers there; later messages
  in that thread continue the same conversation.
* Write and notify actions show Approve / Cancel buttons. Only the person who asked can
  press them (the agent enforces this too).
* A background task delivers due reminders.
* The HTTP API and trace viewer run in the same process, sharing the agent.

The agent is synchronous, so each run happens in a worker thread. Tools that need
Discord call back into the event loop through `DiscordGateway`.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import tasks

from agent.config import Settings, build_agent
from agent.loop import Agent, AgentResult

log = logging.getLogger("bot")
DISCORD_LIMIT = 2000


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split on line breaks so code blocks and the Sources list stay readable."""
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts or [""]


class DiscordGateway:
    """Lets synchronous tools (running in a worker thread) use the async Discord client."""

    def __init__(self, client: discord.Client, loop: asyncio.AbstractEventLoop):
        self.client = client
        self.loop = loop

    def _run(self, coro, timeout: float = 15.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def fetch_messages(self, channel_id: str, limit: int) -> list[dict]:
        async def go():
            ch = self.client.get_channel(int(channel_id)) or await self.client.fetch_channel(int(channel_id))
            msgs = [m async for m in ch.history(limit=limit)]
            return [{"author": m.author.display_name, "content": m.clean_content} for m in reversed(msgs)]

        return self._run(go())

    def post_message(self, channel_id: str, text: str) -> str:
        async def go():
            ch = self.client.get_channel(int(channel_id)) or await self.client.fetch_channel(int(channel_id))
            msg = await ch.send(text[:DISCORD_LIMIT], allowed_mentions=discord.AllowedMentions(users=True))
            return str(msg.id)

        return self._run(go())


class ApprovalView(discord.ui.View):
    def __init__(self, bot: SupportBot, result: AgentResult, requester_id: int):
        super().__init__(timeout=bot.agent.budgets.approval_ttl_seconds)
        self.bot, self.result, self.requester_id = bot, result, requester_id

    async def _decide(self, interaction: discord.Interaction, approved: bool):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the person who asked can approve this.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        label = "Approved" if approved else "Cancelled"
        await interaction.response.edit_message(content=f"{interaction.message.content}\n**{label}.**", view=self)
        async with interaction.channel.typing():
            res = await asyncio.to_thread(
                self.bot.agent.resolve_approval, self.result.run_id, approved, str(interaction.user.id)
            )
        await self.bot.send_result(interaction.channel, res, self.requester_id)
        self.stop()

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._decide(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._decide(interaction, False)


class SupportBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.agent: Agent | None = None
        self.agent_threads: set[int] = set()

    async def setup_hook(self) -> None:
        gateway = DiscordGateway(self, asyncio.get_running_loop())
        self.agent = await asyncio.to_thread(build_agent, self.settings, gateway)
        self.deliver_reminders.start()

    async def on_ready(self):
        log.info("logged in as %s", self.user)

    async def on_message(self, message: discord.Message):
        if message.author.bot or self.agent is None:
            return
        in_agent_thread = isinstance(message.channel, discord.Thread) and message.channel.id in self.agent_threads
        mentioned = self.user in message.mentions
        if not (in_agent_thread or mentioned):
            return
        text = message.clean_content.replace(f"@{self.user.display_name}", "").strip()
        if not text:
            return
        channel = message.channel
        if mentioned and not isinstance(channel, discord.Thread):
            channel = await message.create_thread(name=text[:90] or "question", auto_archive_duration=1440)
        if isinstance(channel, discord.Thread):
            self.agent_threads.add(channel.id)
        async with channel.typing():
            res = await asyncio.to_thread(
                self.agent.handle_message, str(channel.id), str(message.author.id), str(channel.id), text
            )
        await self.send_result(channel, res, message.author.id)

    async def send_result(self, channel, res: AgentResult, requester_id: int):
        if res.status == "awaiting_approval":
            await channel.send(res.text, view=ApprovalView(self, res, requester_id))
            return
        for part in split_message(res.text):
            await channel.send(part, suppress_embeds=True)

    @tasks.loop(seconds=30)
    async def deliver_reminders(self):
        store = self.agent.store
        for r in await asyncio.to_thread(store.due_reminders, self.agent.clock()):
            try:
                ch = self.get_channel(int(r["channel_id"])) or await self.fetch_channel(int(r["channel_id"]))
                await ch.send(
                    f"<@{r['user_id']}> reminder: {r['text']}", allowed_mentions=discord.AllowedMentions(users=True)
                )
                await asyncio.to_thread(store.mark_reminder_sent, r["id"])
            except Exception:
                log.exception("reminder %s failed", r["id"])

    @deliver_reminders.before_loop
    async def _wait_ready(self):
        await self.wait_until_ready()


async def main() -> None:
    import uvicorn

    from api.app import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    if not settings.discord_token:
        raise SystemExit("Set DISCORD_BOT_TOKEN (see .env.example)")
    bot = SupportBot(settings)
    async with bot:
        await bot.login(settings.discord_token)  # login() runs setup_hook(), which builds the agent
        server = uvicorn.Server(
            uvicorn.Config(create_app(lambda: bot.agent), host="0.0.0.0", port=8000, log_level="info")
        )
        await asyncio.gather(bot.connect(), server.serve())


if __name__ == "__main__":
    asyncio.run(main())
