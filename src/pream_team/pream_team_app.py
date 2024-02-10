from datetime import datetime
from typing import List, Tuple, Optional
import asyncio
import os
import urwid
import webbrowser

from pream_team.cache_manager import CacheManager
from pream_team.github_pr_fetcher import GitHubPRFetcher

COLOR_PALETTE = [
    ('button_ready', 'dark green', ''),  
    ('button_draft', 'yellow', ''),  
    ('button_ready_focused', 'dark green,underline', ''),  
    ('button_draft_focused', 'yellow,underline', ''),  
    ('title', 'dark green,bold', ''),
    ('title-empty', 'light gray,bold', ''),
    ('title-updating', 'yellow', '')
]


class PRButton(urwid.Button):
    def __init__(self, pr_title, pr_url, draft, repo):
        """
        A class to represent a button that opens a PR in the browser when clicked.
        :param pr_title: The title of the PR.
        :param pr_url: The URL of the PR.
        :param draft: A boolean indicating whether the PR is a draft.
        :param repo: The name of the repository to which the PR belongs.
        """
        super().__init__("")
        self.pr_title = f"{'[draft]' if draft else '[ready]'} [{repo}] - {pr_title}"
        self.pr_url = pr_url
        s = 'button_draft' if draft else 'button_ready'
        sf = 'button_draft_focused' if draft else 'button_ready_focused'
        self._w = urwid.AttrMap(urwid.SelectableIcon(self.pr_title, 0), s, sf)
        urwid.connect_signal(self, 'click', self.open_pr)

    def open_pr(self, _):
        webbrowser.open(self.pr_url)

class PRGroup(urwid.BoxAdapter):
    """
    A class to represent a group of PRs for a specific user.
    :param user: The username of the user for whom the PRs are being displayed.
    :param prs: A list of PRs to be displayed.
    :param timestamp: The timestamp at which the PRs were last updated.
    """
    def __init__(self, user, prs, timestamp):
        self.user = user
        self.prs = prs
        self.timestamp = timestamp
        self.inner_list_walker = urwid.SimpleFocusListWalker([])
        for pr in prs:
            self._add_pr_button(pr)
        self.inner_list_box = urwid.ListBox(self.inner_list_walker)
        self._update_list_box_title(timestamp)
        super().__init__(self.green_bordered_list_box, height=len(prs) + 2)

    def _add_pr_button(self, pr):
        """
        Add a PR button to the list of PRs.
        :param pr: The PR to be added as a button.
        """
        repo_name = pr["repository_url"].split("/")[-1]
        self.inner_list_walker.append(PRButton(pr['title'], pr['html_url'], pr['draft'], repo_name))

    def _update_list_box_title(self, timestamp):
        """
        Update the title of the list box to include the username and the last update time.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        title = f"{self.user} {timestamp}"
        title_attr = 'title' if self.prs else 'title-empty'
        self.green_bordered_list_box = urwid.AttrMap(urwid.LineBox(self.inner_list_box, title=title), title_attr)
        self.box_widget = self.green_bordered_list_box

    def set_prs(self, prs, timestamp):
        """
        Set the PRs to be displayed in the list box, and update the title of the list box.
        :param prs: A list of PRs to be displayed.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        self.prs = prs
        self.timestamp = timestamp
        self.inner_list_walker.clear()
        for pr in prs:
            self._add_pr_button(pr)
        self._update_list_box_title(timestamp)
        self._invalidate()

    def set_updating_prs_title(self):
        """
        Update the title of the list box to indicate that the PRs are being updated.
        """
        title = f"Updating - {self.user} {self.timestamp}"
        self.green_bordered_list_box = urwid.AttrMap(urwid.LineBox(self.inner_list_box, title), 'title-updating')
        self.box_widget = self.green_bordered_list_box

    def get_user(self):
        return self.user

    def get_num_of_prs(self):
        return len(self.prs)

class PreamTeamApp:
    def __init__(self, token: str, org_name: Optional[str], usernames: List[str], days_back: int, cache_dir: str, update_on_startup: bool) -> None:
        """
        A class to represent the main application.
        :param token: The GitHub API token to be used for authentication.
        :param org_name: The name of the organization for which the PRs are to be fetched.
        :param usernames: A list of usernames for which the PRs are to be fetched.
        :param days_back: The number of days in the past to search for PRs.
        :param cache_dir: The directory in which the cache file is to be stored.
        :param update_on_startup: A boolean indicating whether PRs should be fetched immediately upon startup.
        """
        self.org_name: Optional[str] = org_name
        self.usernames: List[str] = usernames
        self.days_back: int = days_back
        self.token = token
        self._initialize_cache_manager(cache_dir)
        self._setup_ui()
        if update_on_startup:
            asyncio.ensure_future(self.fetchPRs())

    def _initialize_cache_manager(self, cache_dir: str) -> None:
        if os.path.isdir(os.path.dirname(cache_dir)):
            self.cache_manager = CacheManager(cache_dir)
        else:
            self.cache_manager = None

    def _setup_ui(self) -> None:
        self.status: urwid.Text = urwid.Text("", align='center')
        header: urwid.Text = urwid.Text(f"Team PRs opened in the last {self.days_back} days.", align='center')
        help_header: urwid.Text = urwid.Text("q - exit, r - refresh, arrow keys - select PR, enter/left click - open PR in browser", align='center')
        list_box: urwid.Padding = self._create_list_box()
        self.main_frame: urwid.Frame = urwid.Frame(header=urwid.Pile([header, help_header, self.status]), body=list_box)
        self._setup_event_loop()

    def _create_list_box(self) -> urwid.Padding:
        self.list_walker: urwid.SimpleFocusListWalker = urwid.SimpleFocusListWalker([])
        list_box: urwid.ListBox = urwid.ListBox(self.list_walker)
        border_box: urwid.LineBox = urwid.LineBox(list_box)
        return urwid.Padding(border_box, width=100, align='center')

    def _setup_event_loop(self) -> None:
        loop = asyncio.get_event_loop()
        asyncio_event_loop= urwid.AsyncioEventLoop(loop=loop)
        self.main_loop = urwid.MainLoop(self.main_frame, event_loop=asyncio_event_loop, unhandled_input=self._handle_input, palette=COLOR_PALETTE)
        self.pr_groups: List[PRGroup] = self._create_pr_groups()

    def _create_pr_groups(self) -> List[PRGroup]:
        pr_groups: List[PRGroup] = []
        for user in self.usernames:
            timestamp, prs = self._load_user_prs_from_cache(user)
            pr_group: PRGroup = PRGroup(user, prs, timestamp)
            pr_groups.append(pr_group)
            self.list_walker.append(pr_group)
        self.list_walker.sort(reverse=True, key=lambda x: x.height)
        return pr_groups

    def _load_user_prs_from_cache(self, user: str) -> Tuple[str, List]:
        if self.cache_manager:
            data = self.cache_manager.load_prs(user)
            return data.get("timestamp", ''), data.get("prs", [])
        return '', []

    async def fetchPRs(self) -> None:
        self._set_prs_as_updating()
        await self._update_pr_groups()

    def _set_prs_as_updating(self) -> None:
        for pr_group in self.pr_groups:
            pr_group.set_updating_prs_title()

    async def _update_pr_groups(self) -> None:
        self.updating: bool = True
        async with GitHubPRFetcher(self.token) as fetcher:
            for pr_group in self.pr_groups:
                await self._update_single_pr_group(pr_group, fetcher)
        self.updating = False
        self._update_ui_status("")

    async def _update_single_pr_group(self, pr_group: PRGroup, fetcher: GitHubPRFetcher) -> None:
        update_status = lambda x: self._update_ui_status(x)
        prs = await fetcher.get_open_prs_for_user(pr_group.get_user(), self.org_name, self.days_back, update_status)
        if self.cache_manager:
            self.cache_manager.save_prs(pr_group.get_user(), prs, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        pr_group.set_prs(prs, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        self.list_walker.sort(reverse=True, key=lambda x: x.height)

    def _update_ui_status(self, text: str) -> None:
        self.status.set_text(text)
        self.main_loop.draw_screen()

    def _handle_input(self, key: str) -> None:
        if (key == 'r' or key == 'R') and not self.updating:
            self.status.set_text("Refreshing...")
            asyncio.ensure_future(self.fetchPRs())
        elif key == 'q' or key == 'Q':
            raise urwid.ExitMainLoop()

    def run(self) -> None:
        self.main_loop.run()
