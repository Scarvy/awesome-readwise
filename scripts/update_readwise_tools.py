#!/usr/bin/env python3
"""Search GitHub for Readwise API usage and update README."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
GITHUB_API = "https://api.github.com"
SEARCH_QUERIES = [
    '"https://readwise.io/api/v2"',
    '"https://readwise.io/api/v3"',
]
REQUEST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "awesome-readwise-bot",
}


@dataclass
class Repository:
    full_name: str
    description: str

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    def to_markdown(self) -> str:
        description = self.description.strip()
        if description and not description.endswith("."):
            description += "."
        if not description:
            description = "GitHub repository using the Readwise API."
        return f"- [{self.name}](https://github.com/{self.full_name}) - {description}"


def github_token() -> Optional[str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set. API rate limits may apply.", file=sys.stderr)
    return token


def build_request(url: str, token: Optional[str]) -> Request:
    req = Request(url)
    for key, value in REQUEST_HEADERS.items():
        req.add_header(key, value)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def api_get(url: str, token: Optional[str]) -> Dict:
    req = build_request(url, token)
    try:
        with urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            reset = exc.headers.get("X-RateLimit-Reset")
            if reset:
                reset_time = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(reset)))
                raise RuntimeError(
                    f"GitHub API rate limit exceeded. Limit resets at {reset_time} UTC."
                ) from exc
        raise


def search_repositories(token: Optional[str]) -> Set[str]:
    repositories: Set[str] = set()
    for query in SEARCH_QUERIES:
        page = 1
        while True:
            params = urlencode({
                "q": f"{query} in:file",
                "per_page": 100,
                "page": page,
            })
            url = f"{GITHUB_API}/search/code?{params}"
            data = api_get(url, token)
            items = data.get("items", [])
            for item in items:
                repo = item.get("repository", {}).get("full_name")
                if repo:
                    repositories.add(repo.lower())
            if len(items) < 100:
                break
            page += 1
            time.sleep(1)  # Be gentle with the API.
    return repositories


def extract_existing_repositories(readme_text: str) -> Set[str]:
    repos: Set[str] = set()
    for line in readme_text.splitlines():
        start = line.find("https://github.com/")
        while start != -1:
            end = line.find(" ", start)
            end_bracket = line.find(")", start)
            candidates = [pos for pos in (end, end_bracket) if pos != -1]
            if candidates:
                end_pos = min(candidates)
            else:
                end_pos = len(line)
            url = line[start:end_pos]
            parts = urlparse(url).path.strip("/").split("/")
            if len(parts) >= 2:
                owner, name = parts[0], parts[1].removesuffix(".git")
                repos.add(f"{owner}/{name}".lower())
            start = line.find("https://github.com/", end_pos)
    return repos


def fetch_repository_metadata(repos: Iterable[str], token: Optional[str]) -> List[Repository]:
    results: List[Repository] = []
    for full_name in sorted(set(repos)):
        url = f"{GITHUB_API}/repos/{full_name}"
        data = api_get(url, token)
        description = data.get("description") or ""
        canonical_name = data.get("full_name") or full_name
        results.append(Repository(full_name=canonical_name, description=description))
        time.sleep(0.2)
    return results


def insert_into_other_section(readme_text: str, entries: List[str]) -> str:
    lines = readme_text.splitlines()
    try:
        start_index = lines.index("### Other")
    except ValueError:
        raise RuntimeError('Could not locate "### Other" section in README.md')

    insert_index = len(lines)
    for idx in range(start_index + 1, len(lines)):
        line = lines[idx]
        if line.startswith("## ") or line.startswith("### "):
            insert_index = idx
            break

    # Insert before trailing blank lines to keep list continuity.
    while insert_index > start_index and lines[insert_index - 1].strip() == "":
        insert_index -= 1

    insert_lines = list(entries)
    if insert_index > 0 and lines[insert_index - 1].strip() != "":
        insert_lines = ["", *insert_lines]
    updated_lines = lines[:insert_index] + insert_lines + lines[insert_index:]
    return "\n".join(updated_lines) + "\n"


def main() -> int:
    token = github_token()
    readme_text = README_PATH.read_text(encoding="utf-8")
    existing_repos = extract_existing_repositories(readme_text)

    found_repos = search_repositories(token)
    new_repos = sorted(repo for repo in found_repos if repo not in existing_repos)

    if not new_repos:
        print("No new repositories found.")
        return 0

    metadata = fetch_repository_metadata(new_repos, token)
    new_entries = [repo.to_markdown() for repo in metadata]

    updated_readme = insert_into_other_section(readme_text, new_entries)
    README_PATH.write_text(updated_readme, encoding="utf-8")

    print("Added the following repositories:")
    for repo in new_repos:
        print(f" - {repo}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
