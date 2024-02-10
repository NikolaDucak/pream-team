from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import asyncio
import time

GITHUB_API_URL = "https://api.github.com"
REQUEST_BACKOFF_TIME_SECONDS = 60
REQUEST_MAX_RETRIES = 5

async def _sleep_updating(duration, interrupt_after, callback):
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
        await _sleep_updating(sleep_duration, 5, lambda x: status_reporter(f"Primary rate limit hit. Sleeping for {x} seconds"))
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
            await _sleep_updating(backoff_time, 5, lambda x: status_reporter(f"Secondary rate limit hit. Sleeping for {x} seconds."))

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

