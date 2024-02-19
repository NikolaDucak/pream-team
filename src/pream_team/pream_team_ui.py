from typing import Tuple, Optional, Callable
import asyncio
import urwid
import webbrowser
import datetime

from urwid.command_map import enum

from pream_team.cache_manager import CacheManager
from pream_team.github_pr_fetcher import GitHubPRFetcher

COLOR_PALETTE = [
    ("button_ready", "dark green", ""),
    ("button_draft", "yellow", ""),
    ("button_ready_focused", "dark green,underline", ""),
    ("button_draft_focused", "yellow,underline", ""),
    ("title", "dark green,bold", ""),
    ("title-empty", "light gray,bold", ""),
    ("title-updating", "yellow", ""),
]


class MyApprovalStatus(enum.Enum):
    APPROVED = "v"
    COMMENTED = "@"
    CHANGES_REQUESTED = "X"
    NONE = " "
    DISABLED = ""


class PRButton(urwid.Button):

    def styles(self, draft):
        if draft:
            return "button_draft", "button_draft_focused"
        else:
            return "button_ready", "button_ready_focused"

    def __init__(
        self,
        pr_title: str,
        pr_url: str,
        created_at: str,
        draft: bool,
        repo: str,
        approvals: int,
        my_approval_status: MyApprovalStatus,
    ):
        super().__init__("")
        if my_approval_status == MyApprovalStatus.DISABLED:
            self.pr_title = (
                f"[{approvals}] [{'draft' if draft else 'ready'}|{repo}] - {pr_title}"
            )
        else:
            self.pr_title = f"[{my_approval_status.value}|{approvals}] [{'draft' if draft else 'ready'}|{repo}] - {pr_title}"
        self.pr_url = pr_url
        left_widget = urwid.Text(self.pr_title)
        right_widget = urwid.Text(f"{created_at[0:10]}")
        columns = urwid.Columns(
            [
                ("weight", 1, left_widget),
                ("fixed", 10, right_widget),
            ]
        )
        # complete the styles

        n, f = self.styles(draft)
        self._w = urwid.AttrMap(columns, n, f)

        urwid.connect_signal(self, "click", self.open_pr)

    def open_pr(self, _):
        webbrowser.open(self.pr_url)


class PRGroup(urwid.BoxAdapter):
    def __init__(self, user, prs, timestamp, me: Optional[str]):
        """
        A class to represent a group of PRs for a specific user.
        :param user: The username of the user for whom the PRs are being displayed.
        :param prs: A list of PRs to be displayed.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        self.user = user
        self.prs = prs
        self.timestamp = timestamp
        self.inner_list_walker = urwid.SimpleFocusListWalker([])
        inner_list_box = urwid.ListBox(self.inner_list_walker)
        title = f"{self.user} {timestamp}"
        title_attr = "title" if len(self.prs) > 0 else "title-empty"
        self.line_box = urwid.LineBox(inner_list_box, title=title)
        self.line_box_attr_map = urwid.AttrMap(self.line_box, title_attr)
        super().__init__(self.line_box_attr_map, height=len(prs) + 2)
        for pr in prs:
            self._add_pr_button(pr, me)

    def _add_pr_button(self, pr, me: Optional[str]):
        """
        Add a PR button to the list of PRs.
        :param pr: The PR to be added as a button.
        """
        latest_review_time = datetime.datetime(1900, 1, 1)
        my_approval_status = MyApprovalStatus.DISABLED
        if me is not None:
            my_approval_status = MyApprovalStatus.NONE
            me = me.lower()
            for review in pr["reviews"]:
                if review["user"]["login"].lower() == me:
                    current_review_time = datetime.datetime.strptime(
                        review["submitted_at"], "%Y-%m-%dT%H:%M:%SZ"
                    )
                    if current_review_time > latest_review_time:
                        latest_review_time = current_review_time
                        my_approval_status = MyApprovalStatus.CHANGES_REQUESTED
                        if review["state"] == "APPROVED":
                            my_approval_status = MyApprovalStatus.APPROVED
                        elif review["state"] == "COMMENTED":
                            my_approval_status = MyApprovalStatus.COMMENTED

        repo_name = pr["repository_url"].split("/")[-1]
        num_of_approvals = sum(
            it["state"] == "APPROVED" for it in pr.get("reviews", [])
        )
        self.inner_list_walker.append(
            PRButton(
                pr["title"],
                pr["html_url"],
                pr["created_at"],
                pr["draft"],
                repo_name,
                num_of_approvals,
                my_approval_status,
            )
        )

    def _update_list_box_title(self, timestamp):
        """
        Update the title of the list box to include the username and the last update time.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        title = f"{self.user} {timestamp}"
        title_attr = "title" if self.prs else "title-empty"
        self.line_box.set_title(title)
        self.line_box_attr_map.attr_map = {None: title_attr}

    def set_prs(self, prs, timestamp, me: Optional[str]):
        """
        Set the PRs to be displayed in the list box, and update the title of the list box.
        :param prs: A list of PRs to be displayed.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        self.prs = prs
        self.timestamp = timestamp
        self.inner_list_walker.clear()
        for pr in prs:
            self._add_pr_button(pr, me)
        self._update_list_box_title(timestamp)
        self.height = len(prs) + 2
        self._invalidate()

    def set_updating_prs_title(self):
        """
        Update the title of the list box to indicate that the PRs are being updated.
        """
        title = f"Updating - {self.user} {self.timestamp}"
        self.line_box_attr_map.attr_map = {None: "title-updating"}
        self.line_box.set_title(title)

    def get_user(self):
        return self.user

    def get_num_of_prs(self):
        return len(self.prs)


class PreamTeamUI:
    running = False

    def __init__(self, title):
        header: urwid.Text = urwid.Text(title, align="center")
        self.status: urwid.Text = urwid.Text("", align="center")
        help_header: urwid.Text = urwid.Text(
            "q - exit, r - refresh, arrow keys - select PR, enter/left click - open PR in browser",
            align="center",
        )
        list_box: urwid.Padding = self._create_list_box()
        self.main_frame: urwid.Frame = urwid.Frame(
            header=urwid.Pile([header, help_header, self.status]), body=list_box
        )
        self.main_loop = None

    def _create_list_box(self) -> urwid.Padding:
        self.list_walker: urwid.SimpleFocusListWalker = urwid.SimpleFocusListWalker([])
        list_box: urwid.ListBox = urwid.ListBox(self.list_walker)
        border_box: urwid.LineBox = urwid.LineBox(list_box)
        return urwid.Padding(border_box, width=110, align="center")

    def set_all_user_updating(self):
        for pr_group in self.list_walker:
            pr_group.set_updating_prs_title()
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def set_user_pull_requests(
        self, user: str, prs: list, timestamp: str, me: Optional[str]
    ):
        for pr_group in self.list_walker:
            if pr_group.get_user() == user:
                pr_group.set_prs(prs, timestamp, me)
        self.list_walker.sort(key=lambda x: x.get_num_of_prs())
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def add_user(self, user: str, prs: Optional[Tuple[list, str]], me: Optional[str]):
        prs_list, timestamp = (prs[0], prs[1]) if prs is not None else ([], "")
        prg = PRGroup(user, prs_list, timestamp, me)
        self.list_walker.append(prg)
        self.list_walker.sort(key=lambda x: x.get_num_of_prs())
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def set_status(self, status: str):
        self.status.set_text(status)
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def run(self, input_handler):
        loop = asyncio.get_event_loop()
        asyncio_event_loop = urwid.AsyncioEventLoop(loop=loop)
        self.main_loop = urwid.MainLoop(
            self.main_frame,
            event_loop=asyncio_event_loop,
            unhandled_input=input_handler,
            palette=COLOR_PALETTE,
        )
        self.main_loop.run()
