from typing import List, Tuple, Optional, Callable
import asyncio
from datetime import datetime
import webbrowser
import urwid
from urwid.command_map import enum
from pream_team.github_pr_fetcher import PullRequest

COLOR_PALETTE = [
    ("button_ready", "dark green", ""),
    ("button_draft", "yellow", ""),
    ("button_ready_focused", "dark green,underline", ""),
    ("button_draft_focused", "yellow,underline", ""),
    ("title", "dark green,bold", ""),
    ("title-empty", "light gray,bold", ""),
    ("title-updating", "yellow", ""),
]

UI_PR_GROUP_TIMESTAMP_FORMAT = "%Y.%m.%d. %H:%M"
UI_PR_CREATED_AT_TIMESTAMP_FORMAT = "%Y %m %d"


class MyApprovalStatus(enum.Enum):
    APPROVED = "v"
    COMMENTED = "@"
    PENDING = "."
    CHANGES_REQUESTED = "X"
    NONE = " "
    DISABLED = ""


class PRButton(urwid.Button):

    def styles(self, draft):
        if draft:
            return "button_draft", "button_draft_focused"
        return "button_ready", "button_ready_focused"

    def __init__(
        self,
        pr: PullRequest,
        my_approval_status: MyApprovalStatus,
    ):
        super().__init__("")
        self.pr = pr
        self.approvals = (
            f"{pr.num_approvals()}"
            if my_approval_status == MyApprovalStatus.DISABLED
            else f"{my_approval_status.value}|{pr.num_approvals()}"
        )

        draft_str = f"{'draft' if pr.draft else 'ready'}"
        self.pr_title = f"[{self.approvals}] [{draft_str}|{pr.repo}] - {pr.title}"
        left_widget = urwid.Text(self.pr_title)
        right_widget = urwid.Text(
            f"{pr.created_at.strftime(UI_PR_CREATED_AT_TIMESTAMP_FORMAT)}"
        )
        columns = urwid.Columns(
            [
                ("weight", 1, left_widget),
                ("fixed", 10, right_widget),
            ]
        )
        n, f = self.styles(pr.draft)
        self._w = urwid.AttrMap(columns, n, f)

        urwid.connect_signal(self, "click", self.open_pr)

    def open_pr(self, _):
        webbrowser.open(self.pr.url)


def pr_to_prbutton(pr: PullRequest, me: Optional[str]):
    latest_review_time = datetime(year=1900, month=1, day=1)
    my_approval_status = MyApprovalStatus.DISABLED
    if me is not None:
        my_approval_status = MyApprovalStatus.NONE
        me = me.lower()
        for review in pr.reviews:
            if review.user.lower() == me and review.submitted_at is not None:
                current_review_time = review.submitted_at
                if current_review_time > latest_review_time:
                    latest_review_time = current_review_time
                    my_approval_status = MyApprovalStatus.CHANGES_REQUESTED
                    if review.state == "APPROVED":
                        my_approval_status = MyApprovalStatus.APPROVED
                    elif review.state == "COMMENTED":
                        my_approval_status = MyApprovalStatus.COMMENTED
    return PRButton(
        pr,
        my_approval_status,
    )


class PRGroup(urwid.BoxAdapter):
    def __init__(
        self, user, prs: List[PullRequest], timestamp: datetime, me: Optional[str]
    ):
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
        title = f"{self.user} ── {timestamp.strftime(UI_PR_GROUP_TIMESTAMP_FORMAT)}"
        title_attr = "title" if len(self.prs) > 0 else "title-empty"
        self.line_box = urwid.LineBox(inner_list_box, title=title)
        self.line_box_attr_map = urwid.AttrMap(self.line_box, title_attr)
        super().__init__(self.line_box_attr_map, height=len(prs) + 2)
        for pr in prs:
            self._add_pr_button(pr, me)

    def _add_pr_button(self, pr: PullRequest, me: Optional[str]):
        """
        Add a PR button to the list of PRs.
        :param pr: The PR to be added as a button.
        """
        self.inner_list_walker.append(pr_to_prbutton(pr, me))

    def _update_list_box_title(self, timestamp: datetime):
        """
        Update the title of the list box to include the username and the last update time.
        :param timestamp: The timestamp at which the PRs were last updated.
        """
        title = f"{self.user} ── {timestamp.strftime(UI_PR_GROUP_TIMESTAMP_FORMAT)}"
        title_attr = "title" if self.prs else "title-empty"
        self.line_box.set_title(title)
        self.line_box_attr_map.attr_map = {None: title_attr}

    def set_prs(self, prs: List[PullRequest], timestamp: datetime, me: Optional[str]):
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
        title = f"Updating - {self.user} ── {self.timestamp.strftime(UI_PR_GROUP_TIMESTAMP_FORMAT)}"
        self.line_box_attr_map.attr_map = {None: "title-updating"}
        self.line_box.set_title(title)

    def get_user(self):
        return self.user

    def get_num_of_prs(self):
        return len(self.prs)


class PreamTeamUI:
    running = False

    def set_tab(self, body):
        self.main_frame.body = body

    def _create_review_requests_list(self):
        self.review_requests_list_walker = urwid.SimpleFocusListWalker([])
        list_box: urwid.ListBox = urwid.ListBox(self.review_requests_list_walker)
        border_box: urwid.LineBox = urwid.LineBox(list_box)
        return urwid.Padding(border_box, width=110, align="center")

    def set_review_requested_prs(self, prs: List[PullRequest], me: Optional[str]):
        prs = list(set(prs))
        self.review_requests_list_walker.clear()
        for pr in prs:
            self.review_requests_list_walker.append(pr_to_prbutton(pr, me))
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def __init__(self, title: str):
        header: urwid.Text = urwid.Text(title, align="center")
        self.status: urwid.Text = urwid.Text("", align="center")
        help_header: urwid.Text = urwid.Text(
            "q - exit, r - refresh, arrow keys - select PR, enter/left click - open PR in browser",
            align="center",
        )
        team_prs_list_list: urwid.Padding = self._create_list_box()
        review_requests_list = self._create_review_requests_list()
        tabs = urwid.Columns(
            widget_list=[
                urwid.Button(
                    "Team PRs",
                    on_press=lambda _: self.set_tab(team_prs_list_list),
                    align="center",
                ),
                urwid.Button(
                    "Review requested",
                    on_press=lambda _: self.set_tab(review_requests_list),
                    align="center",
                ),
            ]
        )
        self.main_frame: urwid.Frame = urwid.Frame(
            header=urwid.Pile([header, help_header, tabs, self.status]),
            body=team_prs_list_list,
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
        self, user: str, prs: List[PullRequest], timestamp: datetime, me: Optional[str]
    ):
        for pr_group in self.list_walker:
            if pr_group.get_user() == user:
                pr_group.set_prs(prs, timestamp, me)
        self.list_walker.sort(key=lambda x: x.get_num_of_prs())
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def add_user(
        self,
        user: str,
        prs: Optional[Tuple[list[PullRequest], datetime]],
        me: Optional[str],
    ):
        prs_list, timestamp = (
            prs if prs is not None else ([], datetime(year=1, month=1, day=1))
        )
        prg = PRGroup(user, prs_list, timestamp, me)
        self.list_walker.append(prg)
        self.list_walker.sort(key=lambda x: x.get_num_of_prs())
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def set_status(self, status: str):
        self.status.set_text(status)
        if self.main_loop is not None:
            self.main_loop.draw_screen()

    def run(self, input_handler: Callable):
        loop = asyncio.get_event_loop()
        asyncio_event_loop = urwid.AsyncioEventLoop(loop=loop)
        self.main_loop = urwid.MainLoop(
            self.main_frame,
            event_loop=asyncio_event_loop,
            unhandled_input=input_handler,
            palette=COLOR_PALETTE,
        )
        self.main_loop.run()
