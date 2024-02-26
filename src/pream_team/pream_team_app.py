from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Callable
import asyncio
import urwid

from pream_team.cache_manager import CacheManager
from pream_team.github_pr_fetcher import (
    GitHubPRFetcher,
    PullRequest,
    raw_pr_info_to_pr_list,
)
from pream_team.pream_team_ui import PreamTeamUI

CACHE_CLEANUP_OLDER_THAN = timedelta(days=10)


def make_cache_key_for_review_request(username: str):
    """
    Returns a cache key for a user's review requests.
    :param username: The username of the user for whom the review requests are being cached.
    """
    return "requested:" + username


class PreamTeamApp:
    def __init__(
        self,
        fetcher_factory: Callable[[], GitHubPRFetcher],
        cache_manager: Optional[CacheManager],
        ui: PreamTeamUI,
        usernames: List[str],
        update_on_startup: bool,
        me: Optional[str],
        my_team: Optional[str],
        days_back: int,
    ) -> None:
        self.me = me
        self.my_team = my_team
        self.usernames: List[str] = usernames
        self.cache_manager = cache_manager
        self.ui = ui
        self.fetcher_factory = fetcher_factory
        self.days_back = days_back
        self.updating = False
        self._display_cached_prs()
        if update_on_startup:
            asyncio.ensure_future(self._fetch_prs())
        if self.cache_manager:
            self.cache_manager.clean_up(CACHE_CLEANUP_OLDER_THAN)

    def _display_cached_prs(self):
        for user in self.usernames:
            data = self._load_prs_from_cache(user)
            self.ui.add_user(user, data, self.me)

        reqs: List[PullRequest] = []
        if self.cache_manager and self.my_team:
            data = self._load_prs_from_cache("requested:" + self.my_team)
            if data:
                prs, _ = data
                reqs.extend(prs)
        if self.cache_manager and self.me:
            data = self._load_prs_from_cache("requested:" + self.me)
            if data:
                prs, _ = data
                reqs.extend(prs)

        self.ui.set_review_requested_prs(reqs, self.me)

    def _load_prs_from_cache(
        self, user: str
    ) -> Optional[Tuple[List[PullRequest], datetime]]:
        if self.cache_manager:
            data = self.cache_manager.load_prs(user)
            if data:
                limit = datetime.now() - timedelta(days=self.days_back)
                filtered_prs = [
                    pr
                    for pr in raw_pr_info_to_pr_list(data.prs)
                    if pr.created_at > limit
                ]
                return filtered_prs, data.timestamp
        return None

    async def _fetch_prs(self) -> None:
        self.ui.set_all_user_updating()
        await self._update_pr_list()

    async def _update_pr_list(self) -> None:
        self.updating: bool = True
        async with self.fetcher_factory() as fetcher:
            for user in self.usernames:
                await self._update_single_prs_for_user(user, fetcher)

            def update_status(x):
                self.ui.set_status(x)

            res: List[PullRequest] = []
            if self.me is not None:
                data = await fetcher.get_prs_with_review_request_user(
                    self.me, update_status
                )
                if self.cache_manager:
                    self.cache_manager.save_prs(
                        "requested:" + self.me, data, datetime.utcnow()
                    )
                res.extend(raw_pr_info_to_pr_list(data))
            if self.my_team is not None:
                data = await fetcher.get_prs_with_review_request_team(
                    self.my_team, update_status
                )
                if self.cache_manager:
                    self.cache_manager.save_prs(
                        "requested:" + self.my_team, data, datetime.utcnow()
                    )
                res.extend(raw_pr_info_to_pr_list(data))

            self.ui.set_review_requested_prs(res, self.me)
        self.updating = False
        self.ui.set_status("")

    async def _update_single_prs_for_user(
        self, user: str, fetcher: GitHubPRFetcher
    ) -> None:
        def update_status(x):
            self.ui.set_status(x)

        prs = await fetcher.get_open_prs_for_user(user, update_status)
        if self.cache_manager:
            self.cache_manager.save_prs(user, prs, datetime.utcnow())
        prs = raw_pr_info_to_pr_list(prs)
        self.ui.set_user_pull_requests(user, prs, datetime.utcnow(), self.me)

    def _handle_input(self, key: str) -> None:
        if key in ("r", "R") and not self.updating:
            self.ui.set_status("Refreshing...")
            asyncio.ensure_future(self._fetch_prs())
        elif key == "tab":
            self.ui.toggle_focus()
        elif key in ("q", "Q"):
            raise urwid.ExitMainLoop()
        # vim navigation bindings


    def run(self) -> None:
        def handler(x):
            self._handle_input(x)

        self.ui.run(handler)
