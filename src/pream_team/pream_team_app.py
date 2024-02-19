from datetime import datetime
from typing import List, Tuple, Optional, Callable
import asyncio
import urwid

from pream_team.cache_manager import CacheManager
from pream_team.github_pr_fetcher import GitHubPRFetcher
from pream_team.pream_team_ui import PreamTeamUI

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"



class PreamTeamApp:
    def __init__(
        self,
        fetcher_factory: Callable[[], GitHubPRFetcher],
        cache_manager: Optional[CacheManager],
        ui: PreamTeamUI,
        usernames: List[str],
        update_on_startup: bool,
        me: Optional[str],
    ) -> None:
        self.me = me
        self.usernames: List[str] = usernames
        self.cache_manager = cache_manager
        self.ui = ui
        self.fetcher_factory = fetcher_factory
        self._display_cached_prs()
        if update_on_startup:
            asyncio.ensure_future(self._fetch_prs())

    def _display_cached_prs(self):
        for user in self.usernames:
            timestamp, prs = self._load_user_prs_from_cache(user)
            self.ui.add_user(user, (prs, timestamp), self.me)

    def _load_user_prs_from_cache(self, user: str) -> Tuple[str, List]:
        if self.cache_manager:
            data = self.cache_manager.load_prs(user)
            return data.get("timestamp", ""), data.get("prs", [])
        return "", []

    async def _fetch_prs(self) -> None:
        self.ui.set_all_user_updating()
        await self._update_pr_list()

    async def _update_pr_list(self) -> None:
        self.updating: bool = True
        async with self.fetcher_factory() as fetcher:
            for user in self.usernames:
                await self._update_single_prs_for_user(user, fetcher)
        self.updating = False
        self.ui.set_status("")

    async def _update_single_prs_for_user(
        self, user: str, fetcher: GitHubPRFetcher
    ) -> None:
        update_status = lambda x: self.ui.set_status(x)
        prs = await fetcher.get_open_prs_for_user(user, update_status)
        if self.cache_manager:
            self.cache_manager.save_prs(
                user, prs, datetime.utcnow().strftime(TIMESTAMP_FORMAT)
            )
        self.ui.set_user_pull_requests(
            user, prs, datetime.utcnow().strftime(TIMESTAMP_FORMAT), self.me
        )

    def _handle_input(self, key: str) -> None:
        if key in ("r", "R") and not self.updating:
            self.ui.set_status("Refreshing...")
            asyncio.ensure_future(self._fetch_prs())
        elif key in ("q", "Q"):
            raise urwid.ExitMainLoop()

    def run(self) -> None:
        self.ui.run(lambda input: self._handle_input(input))
