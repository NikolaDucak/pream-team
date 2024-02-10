from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import aiohttp
import argparse
import asyncio
import json
import os
import time
import urwid
import webbrowser
import yaml

GITHUB_API_URL = "https://api.github.com"
COLOR_PALETTE = [
    ('button_ready', 'dark green', ''),  
    ('button_draft', 'yellow', ''),  
    ('button_ready_focused', 'dark green,underline', ''),  
    ('button_draft_focused', 'yellow,underline', ''),  
    ('title', 'dark green,bold', ''),
    ('title-empty', 'light gray,bold', ''),
    ('title-updating', 'yellow', '')
]
REQUEST_BACKOFF_TIME_SECONDS = 60
REQUEST_MAX_RETRIES = 5


class CacheManager:
    def __init__(self, cache_file_path: str):
        """
        A class to manage caching of PRs to a file.
        The cache is stored as a JSON object with the following structure
        :param cache_file_path: The path to the file where the cache will be stored.
        """
        self.cache_file_path = cache_file_path
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict]:
        """
        Load the cache from the specified file path.
        Returns an empty dictionary if the file does not exist or is empty.
        """
        try:
            with open(self.cache_file_path, 'r') as file:
                return json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_prs(self, user: str, prs: List[Dict], timestamp):
        """
        Save the PRs for a specific user to the cache file, including the timestamp of when the PRs were saved.
        param: user: The username of the user for whom the PRs are being saved.
        param: prs: The PRs to be saved.
        param: timestamp: The timestamp of when the PRs were saved.
        """
        self.cache[user] = {
            "timestamp": timestamp,
            "prs": prs
        }
        try:
            with open(self.cache_file_path, 'w') as file:
                json.dump(self.cache, file, indent=4)
        except (FileNotFoundError, json.JSONDecodeError):
            exit(1)

    def load_prs(self, user: str) -> Dict:
        """
        Load the PRs for a specific user from the cache.
        Returns an empty dictionary if there is no cached data for the user.
        :param user: The username of the user for whom the PRs are being loaded.
        """
        return self.cache.get(user, {})


async def sleep_updating(duration, interrupt_after, callback):
    """
    A helper function to sleep for a given duration, updating a callback function with the remaining time at regular intervals.
    with the remaining time at regular intervals.
    :param duration: The total duration for which to sleep.
    :param interrupt_after: The interval at which the callback function should be updated.
    :caram callback: The callback function to be updated with the remaining time.
    """
    while duration > 0:
        sleep_time = min(duration, interrupt_after)
        callback(duration)
        await asyncio.sleep(sleep_time)
        duration -= sleep_time
    

class GitHubPRFetcher:
    def __init__(self, token: str):
        """
        A class to fetch GitHub PRs for a specific user from the GitHub API.
        :param token: The GitHub API token to be used for authentication.
        """
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.session = None  # Initialized later in the __aenter__ method

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session != None:
            await self.session.close()

    async def _primary_rate_limit_retry(self, reset_time: int, request: str, session: aiohttp.ClientSession, status_reporter) -> aiohttp.ClientResponse:
        """
        A helper function to handle the primary rate limit, which is triggered when the rate limit is hit.
        This function sleeps until the rate limit is reset, then retries the request.
        :param reset_time: The time at which the rate limit will be reset.
        :param request: The request to be retried.
        :param session: The aiohttp ClientSession to use for making the request.
        :param status_reporter: The callback function where status messages will be sent.
        :return: The response from the request.
        """
        sleep_duration = reset_time - time.time() + 5  # Add 5 seconds buffer
        await sleep_updating(sleep_duration, 5, lambda x: status_reporter(f"Primary rate limit hit. Sleeping for {x} seconds"))
        return await session.get(request)  # Retry the request

    async def _secondary_rate_limit_exponential_backoff(self, request: str, session: aiohttp.ClientSession, status_reporter) -> Optional[aiohttp.ClientResponse]:
        """
        A helper function to handle the secondary rate limit, which is triggered when the primary rate limit is hit.
        This function uses an exponential backoff strategy to retry the request multiple times if the rate limit is still in effect.
        :param request: The request to be retried.
        :param session: The aiohttp ClientSession to use for making the request.
        :param status_reporter: The callback function where status messages will be sent.
        :return: The response from the request, or None if the request fails after multiple retries.
        """
        backoff_time = REQUEST_BACKOFF_TIME_SECONDS
        retries = 0

        while retries < REQUEST_MAX_RETRIES:
            await sleep_updating(backoff_time, 5, lambda x: status_reporter(f"Secondary rate limit hit. Sleeping for {x} seconds."))

            try:
                response = await session.get(request)

                if response.status == 200:
                    return response

                if response.status == 422:
                    response_json = await response.json()
                    status_reporter(f"[ERR] Validation failed. Reason: {response_json.get('message')}")

                if response.status != 403:
                    response_json = await response.json()
                    status_reporter(f"Received response: {response.status} {response_json.get('message')}")

                backoff_time *= 2  # Double the wait time for the next iteration
                retries += 1
            except Exception as e:
                status_reporter(f"[ERR] Exception occurred during secondary rate limit exponential backoff: {e}")
                await asyncio.sleep(backoff_time)
                backoff_time *= 2
                retries += 1

        # If we've exhausted retries, return None to indicate failure
        return None


    async def get_open_prs_for_user(self, username: str, org: Optional[str], days_back: int, status_reporter) -> List[Dict[str, str]]:
        """
        Fetch open PRs for a specific user from a specific organization.
        :param username: The username of the user for whom the PRs are to be fetched.
        :param org: The name of the organization for which the PRs are to be fetched.
        :param days_back: The number of days in the past to search for PRs.
        :param status_reporter: The callback function to be updated with status messages.
        :return: A list of open PRs for the specified user and organization.
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        date_filter = f"{start_date.strftime('%Y-%m-%d')}..{end_date.strftime('%Y-%m-%d')}"
        req_str = ""
        if org is None:
            req_str = f"{GITHUB_API_URL}/search/issues?q=author:{username}+type:pr+is:open+created:{date_filter}"
        else:
            req_str = f"{GITHUB_API_URL}/search/issues?q=author:{username}+org:{org}+type:pr+is:open+created:{date_filter}"

        if self.session == None:
            raise Exception("Session not initialized. Use 'async with' to initialize the session.")

        status_reporter(f"Fetching prs for {username}...")
        async with self.session.get(req_str) as response:
            # Primary Rate Limit Check
            if response.status == 403 and 'X-RateLimit-Remaining' in response.headers and int(response.headers['X-RateLimit-Remaining']) == 0:
                reset_time = int(response.headers['X-RateLimit-Reset'])
                response = await self._primary_rate_limit_retry(reset_time, req_str, self.session,status_reporter)

            # Secondary Rate Limit Check
            if response.status == 403 and "secondary rate limit" in (await response.json()).get('message', ''):
                response = await self._secondary_rate_limit_exponential_backoff(req_str, self.session, status_reporter)

            if response == None or response.status not in [200, 403]:
                status_reporter("Error during request :/")
                return []

            await asyncio.sleep(2.4) # this should help with rate limitihg
            return (await response.json()).get("items", [])


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

class App:
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

def parse_args() -> Tuple[str, Optional[str], List[str], int, str, bool]:
    parser = argparse.ArgumentParser(description="Fetch GitHub PRs for specific users from the past N days.")
    parser.add_argument("--names", nargs='+', help="List of GitHub usernames.")
    parser.add_argument("--days", type=int, help="Number of past days to search for PRs.")
    parser.add_argument("--token", type=str, help="GitHub API token.")
    parser.add_argument("--org", type=str, help="GitHub organization name.")
    parser.add_argument("--file", type=str, default=os.path.expanduser("~/.prs/config.yml"), 
                        help="Path to YAML file containing 'names', 'days', 'token' and 'org' fields. " +
                        "(Note that command line arguments override YAML file configuration)")

    args = parser.parse_args()

    token, org_name, usernames, days_back, cache_file_path, update_on_startup = "", None, [], 30, "", True

    if os.path.exists(args.file):
        with open(args.file, 'r') as f:
            data = yaml.safe_load(f)
            token = data.get('token', "")
            org_name = data.get('org', None)
            usernames = data.get("names", [])
            days_back = data.get("days-back", 30)
            cache_file_path = data.get("cache_dir", os.environ["HOME"]+"/.prs/cache.json")
            update_on_startup = data.get("update_on_startup", True)

    if args.token:
        token = args.token
    if args.org:
        org_name = args.org
    if args.names:
        usernames = args.names
    if args.days:
        days_back = args.days

    if not usernames or not token:
        print("Token and Usernames must be provided.")
        exit(1)

    return token, org_name, usernames, days_back, cache_file_path, update_on_startup


def app_main():
    token, org_name, usernames, days_back, cache_file_path, update_on_startup = parse_args()
    app = App(token, org_name, usernames, days_back, cache_file_path, update_on_startup)
    app.run()


if __name__ == "__main__":
    app_main()
