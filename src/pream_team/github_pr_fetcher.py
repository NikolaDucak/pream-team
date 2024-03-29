from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Union
import asyncio
import time
import aiohttp


GITHUB_API_URL = "https://api.github.com"
GITHUB_REVIEW_SUBMITTED_AT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
GITHUB_PR_CREATED_AT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REQUEST_BACKOFF_TIME_SECONDS = 60
REQUEST_MAX_RETRIES = 5

ReviewState = Union[
    Literal["COMMENTED"],
    Literal["PENDING"],
    Literal["CHANGES_REQUESTED"],
    Literal["APPROVED"],
]


class Review:
    def __init__(
        self, user: str, state: ReviewState, submitted_at: Optional[datetime]
    ) -> None:
        self.user = user
        self.state = state
        self.submitted_at = submitted_at

    def __hash__(self):
        return hash((self.user, self.state, self.submitted_at))

    def __eq__(self, other):
        if isinstance(other, Review):
            return (
                self.user == other.user
                and self.state == other.state
                and self.submitted_at == other.submitted_at
            )
        return False

    def __repr__(self):
        return f"Review(user={self.user}, state={self.state}, submitted_at={self.submitted_at})"


class PullRequest:
    # constructor taking all params
    def __init__(
        self,
        title: str,
        author: str,
        url: str,
        draft: bool,
        repo: str,
        created_at: datetime,
        reviews: List[Review],
    ) -> None:
        self.title = title
        self.author = author
        self.url = url
        self.draft = draft
        self.url = url
        self.repo = repo
        self.created_at = created_at
        self.reviews = reviews

    def num_approvals(self):
        return len([r for r in self.reviews if r.state == "APPROVED"])

    def __hash__(self):
        # Assuming the URL uniquely identifies a PullRequest
        return hash(self.url)

    def __eq__(self, other):
        if isinstance(other, PullRequest):
            return self.url == other.url
        return False

    def __repr__(self):
        return f"PullRequest(title={self.title}, author={self.author}, url={self.url})"


def raw_pr_info_to_pr_list(api_response: Any) -> List[PullRequest]:
    prs = []
    for pr_dict in api_response:
        title = pr_dict.get("title", "")
        author = pr_dict.get("user", {}).get("login", "")
        url = pr_dict.get("html_url", "")
        draft = pr_dict.get("draft", False)
        repo = pr_dict.get("repository_url").split("/")[-1]
        created_at = datetime.strptime(
            pr_dict.get("created_at", ""), GITHUB_PR_CREATED_AT_TIMESTAMP_FORMAT
        )
        reviews = []
        for review in pr_dict.get("reviews", []):
            user = review.get("user", {}).get("login", "")
            state = review.get("state", "")
            submitted_at = review.get("submitted_at", None)
            if submitted_at is not None:
                submitted_at = datetime.strptime(
                    submitted_at, GITHUB_REVIEW_SUBMITTED_AT_TIMESTAMP_FORMAT
                )
            reviews.append(Review(user, state, submitted_at))
        prs.append(PullRequest(title, author, url, draft, repo, created_at, reviews))
    return prs


async def _sleep_updating(duration, interrupt_after, callback):
    """
    A helper function to sleep for a given duration, updating a callback
    function with the remaining time at regular intervals.
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


class GitHubApprovalFetcher:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        """
        A class to fetch approvals for a specific PR from the GitHub API.
        :param session: The aiohttp ClientSession to use for making the request.
        """
        self.session: aiohttp.ClientSession = session

    async def fetch(self, pr_link: str) -> List[Dict[str, str]]:
        """
        Fetch the approvals for a specific PR from the GitHub API.
        :param pr_link: The link to the PR for which approvals are to be fetched.
        :return: A list of approvals for the specified PR.
        """
        approvals: List[Dict[str, str]] = []

        async with self.session.get(pr_link + "/reviews") as response:
            if response.status == 200:
                approvals_data: List[Dict] = await response.json()
                approvals = [
                    review
                    for review in approvals_data
                    if review.get("state") == "APPROVED"
                ]

        return approvals


class GitHubPRFetcher:
    def __init__(self, token: str, org: Optional[str], days_back: int):
        """
        A class to fetch GitHub PRs for a specific user from the GitHub API.
        :param token: The GitHub API token to be used for authentication.
        """
        self.org = org
        self.days_back = days_back
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        self.session = None  # Initialized later in the __aenter__ method

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session is not None:
            await self.session.close()

    async def get_open_prs_for_user(self, username: str, status_reporter) -> Any:
        """
        Fetch open PRs for a specific user from a specific organization.
        :param username: The username of the user for whom the PRs are to be fetched.
        :param org: The name of the organization for which the PRs are to be fetched.
        :param days_back: The number of days in the past to search for PRs.
        :param status_reporter: The callback function to be updated with status messages.
        :return: A list of open PRs for the specified user and organization.
        """
        date_filter = self._make_time_filter()
        org_str = f"+org:{self.org}" if self.org is not None else ""
        req_str = (
            f"{GITHUB_API_URL}/search/issues?q=author:{username}{org_str}"
            + f"+type:pr+is:open+created:{date_filter}"
        )

        status_reporter(f"Fetching open prs for {username}")
        result = await self._run_call(req_str, status_reporter)
        return result

    async def get_prs_with_review_request_user(
        self, username: str, status_reporter
    ) -> List[Any]:
        """
        Fetch PRs that requested reviews from user.
        :param username: The username of the user whose review is being requested .
        :param status_reporter: The callback function to be updated with status messages.
        :return: A list of PRs with review requests for the specified user.
        """
        date_filter = self._make_time_filter()
        org_str = f"+org:{self.org}" if self.org is not None else ""
        req_str = (
            f"{GITHUB_API_URL}/search/issues?q=is:pr+review-requested:{username}"
            + f"{org_str}+created:{date_filter}+is:open"
        )
        status_reporter(f"Fetching review requested prs for {username}")
        result = await self._run_call(req_str, status_reporter)
        return result

    async def get_prs_with_review_request_team(
        self, teamname: str, status_reporter
    ) -> List[Any]:
        """
        Fetch PRs that requested reviews from the specified team.
        :param username: The username of the user whose review is being requested .
        :param status_reporter: The callback function to be updated with status messages.
        :return: A list of PRs with review requests for the specified user.
        """
        date_filter = self._make_time_filter()
        org_str = f"+org:{self.org}" if self.org is not None else ""
        req_str = (
            f"{GITHUB_API_URL}/search/issues?q=is:pr+team-review-requested:{teamname}"
            + f"{org_str}+created:{date_filter}+is:open"
        )
        status_reporter(f"Fetching review requested prs for {teamname}")
        result = await self._run_call(req_str, status_reporter)
        return result

    async def _get_approvals_for_pr(self, pr_link: str) -> List[Dict[str, str]]:
        if self.session is None:
            raise Exception(
                "Session not initialized. Use 'async with' to initialize the session."
            )
        approval_fetcher: GitHubApprovalFetcher = GitHubApprovalFetcher(self.session)
        return await approval_fetcher.fetch(pr_link)

    def _make_time_filter(self):
        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.days_back)
        return f"{start_date.strftime('%Y-%m-%d')}..{end_date.strftime('%Y-%m-%d')}"

    async def _run_call(self, req_str, status_reporter) -> List[Any]:
        if self.session is None:
            raise Exception(
                "Session not initialized. Use 'async with' to initialize the session."
            )

        prs_data: Dict = {}
        async with self.session.get(req_str) as response:
            # Primary Rate Limit Check
            if (
                response.status == 403
                and "X-RateLimit-Remaining" in response.headers
                and int(response.headers["X-RateLimit-Remaining"]) == 0
            ):
                reset_time = int(response.headers["X-RateLimit-Reset"])
                response = await self._primary_rate_limit_retry(
                    reset_time, req_str, self.session, status_reporter
                )

            # Secondary Rate Limit Check
            if response.status == 403 and "secondary rate limit" in (
                await response.json()
            ).get("message", ""):
                response = await self._secondary_rate_limit_exponential_backoff(
                    req_str, self.session, status_reporter
                )

            if response is None or response.status not in [200, 403]:
                status_reporter("Error during request :/")
                return []
            prs_data: Dict = await response.json()

        prs: List[Dict[str, Any]] = prs_data.get("items", [])
        for pr in prs:
            reviews_url: str = pr.get("pull_request", {}).get("url", "") + "/reviews"
            async with self.session.get(reviews_url) as reviews_response:
                if reviews_response.status == 200:
                    reviews_data: List[Dict] = await reviews_response.json()
                    pr["reviews"] = reviews_data
                else:
                    pr["reviews"] = []
        return prs

    async def _primary_rate_limit_retry(
        self,
        reset_time: int,
        request: str,
        session: aiohttp.ClientSession,
        status_reporter,
    ) -> aiohttp.ClientResponse:
        """
        A helper function to handle the primary rate limit, which is
        triggered when the rate limit is hit.
        This function sleeps until the rate limit is reset, then retries the request.
        :param reset_time: The time at which the rate limit will be reset.
        :param request: The request to be retried.
        :param session: The aiohttp ClientSession to use for making the request.
        :param status_reporter: The callback function where status messages will be sent.
        :return: The response from the request.
        """
        sleep_duration = reset_time - time.time() + 5  # Add 5 seconds buffer
        await _sleep_updating(
            sleep_duration,
            5,
            lambda x: status_reporter(
                f"Primary rate limit hit. Sleeping for {x} seconds"
            ),
        )
        return await session.get(request)  # Retry the request

    async def _secondary_rate_limit_exponential_backoff(
        self, request: str, session: aiohttp.ClientSession, status_reporter
    ) -> Optional[aiohttp.ClientResponse]:
        """
        A helper function to handle the secondary rate limit, which is triggered
        when the primary rate limit is hit.
        This function uses an exponential backoff strategy to retry the request
        multiple times if the rate limit is still in effect.
        :param request: The request to be retried.
        :param session: The aiohttp ClientSession to use for making the request.
        :param status_reporter: The callback function where status messages will be sent.
        :return: The response from the request, or None if the request fails
        after multiple retries.
        """
        backoff_time = REQUEST_BACKOFF_TIME_SECONDS
        retries = 0

        while retries < REQUEST_MAX_RETRIES:
            await _sleep_updating(
                backoff_time,
                5,
                lambda x: status_reporter(
                    f"Secondary rate limit hit. Sleeping for {x} seconds."
                ),
            )

            try:
                response = await session.get(request)

                if response.status == 200:
                    return response

                if response.status == 422:
                    response_json = await response.json()
                    status_reporter(
                        f"[ERR] Validation failed. Reason: {response_json.get('message')}"
                    )

                if response.status != 403:
                    response_json = await response.json()
                    status_reporter(
                        f"Received response: {response.status} {response_json.get('message')}"
                    )

                backoff_time *= 2  # Double the wait time for the next iteration
                retries += 1
            except Exception as e:
                status_reporter(
                    f"[ERR] Exception occurred during secondary rate limit exponential backoff: {e}"
                )
                await asyncio.sleep(backoff_time)
                backoff_time *= 2
                retries += 1

        return None
