"""Repository providers for the GitHub MCP server.

- `GitHubRestProvider`: the GitHub REST API (token-authenticated when configured).
- `LocalGitProvider`: a local git repository read with `git` (no shell; validated refs and paths). Used for the
  deterministic demo repositories so the local demo works offline. It is a real git implementation, not a mock.
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from opspilot.errors import NotFoundError, PermissionDeniedError, UpstreamUnavailableError, ValidationFailedError

MAX_FILE_BYTES = 100_000
MAX_PATCH_CHARS = 20_000
MAX_DIFF_FILES = 50

_REF = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/\-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_PATH = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9._/\- ]{1,300}$")
_REPO = re.compile(r"^[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}$")


def validate_ref(ref: str) -> str:
    if not _REF.match(ref):
        raise ValidationFailedError("Invalid git ref")
    return ref


def validate_sha(sha: str) -> str:
    if not _SHA.match(sha):
        raise ValidationFailedError("Commit SHA must be 7-40 lowercase hex characters")
    return sha


def validate_path(path: str) -> str:
    if not _PATH.match(path):
        raise ValidationFailedError("Invalid repository path")
    return path


def validate_search_query(query: str) -> str:
    """Search terms only. GitHub search qualifiers (e.g. `repo:`, `org:`) could widen the search beyond the
    allow-listed repository, so any token containing ':' is rejected."""
    q = " ".join(query.split())
    if not q or len(q) > 200 or any(":" in tok for tok in q.split()):
        raise ValidationFailedError("Search query must be 1-200 characters of plain terms (no qualifiers)")
    return q


class CodeMatch(BaseModel):
    path: str
    line: int | None
    snippet: str


class FileContent(BaseModel):
    repository: str
    path: str
    ref: str
    size_bytes: int
    truncated: bool
    content: str


class CommitSummary(BaseModel):
    sha: str
    author: str
    date: str
    message: str


class FileChange(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None
    patch_truncated: bool = False


class CommitDiff(BaseModel):
    repository: str
    sha: str
    author: str
    date: str
    message: str
    files: list[FileChange]


class PullRequest(BaseModel):
    repository: str
    number: int
    title: str
    state: str
    author: str
    merged: bool
    merge_commit_sha: str | None
    created_at: str
    merged_at: str | None
    body: str
    files: list[FileChange]


class RepoProvider(Protocol):
    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeMatch]: ...
    async def get_file(self, repo: str, path: str, ref: str) -> FileContent: ...
    async def recent_commits(self, repo: str, ref: str, limit: int, path: str | None) -> list[CommitSummary]: ...
    async def pull_request(self, repo: str, number: int) -> PullRequest: ...
    async def commit_diff(self, repo: str, sha: str) -> CommitDiff: ...


def _truncate_patch(patch: str | None) -> tuple[str | None, bool]:
    if patch is None or len(patch) <= MAX_PATCH_CHARS:
        return patch, False
    return patch[:MAX_PATCH_CHARS], True


class GitHubRestProvider:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opspilot-mcp",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._authenticated = bool(token)
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, transport=transport)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamUnavailableError(f"GitHub API unreachable: {exc.__class__.__name__}") from exc
        if r.status_code == 404:
            raise NotFoundError("Not found on GitHub (or not visible to the configured token)")
        if r.status_code in (401, 403, 429):
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining == "0" or r.status_code == 429:
                raise UpstreamUnavailableError(
                    "GitHub rate limit exceeded", details={"reset": r.headers.get("x-ratelimit-reset")}
                )
            raise PermissionDeniedError(f"GitHub denied the request ({r.status_code})")
        if r.status_code == 422:
            raise ValidationFailedError("GitHub rejected the request parameters")
        if r.status_code >= 400:
            raise UpstreamUnavailableError(f"GitHub API error {r.status_code}")
        return r.json()

    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeMatch]:
        if not self._authenticated:
            raise PermissionDeniedError("GitHub code search requires a token (OPSPILOT_GITHUB_TOKEN)")
        data = await self._get("/search/code", {"q": f"{validate_search_query(query)} repo:{repo}", "per_page": limit})
        return [CodeMatch(path=i["path"], line=None, snippet=i.get("name", "")) for i in data.get("items", [])[:limit]]

    async def get_file(self, repo: str, path: str, ref: str) -> FileContent:
        data = await self._get(f"/repos/{repo}/contents/{validate_path(path)}", {"ref": validate_ref(ref)})
        if isinstance(data, list) or data.get("type") != "file":
            raise ValidationFailedError("Path is a directory, not a file")
        raw = base64.b64decode(data.get("content", "")) if data.get("encoding") == "base64" else b""
        text = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        return FileContent(
            repository=repo,
            path=path,
            ref=ref,
            size_bytes=int(data.get("size", len(raw))),
            truncated=len(raw) > MAX_FILE_BYTES,
            content=text,
        )

    async def recent_commits(self, repo: str, ref: str, limit: int, path: str | None) -> list[CommitSummary]:
        params: dict[str, Any] = {"sha": validate_ref(ref), "per_page": limit}
        if path:
            params["path"] = validate_path(path)
        data = await self._get(f"/repos/{repo}/commits", params)
        return [
            CommitSummary(
                sha=c["sha"],
                author=c["commit"]["author"]["name"],
                date=c["commit"]["author"]["date"],
                message=c["commit"]["message"][:2000],
            )
            for c in data[:limit]
        ]

    async def pull_request(self, repo: str, number: int) -> PullRequest:
        pr = await self._get(f"/repos/{repo}/pulls/{number}")
        files = await self._get(f"/repos/{repo}/pulls/{number}/files", {"per_page": MAX_DIFF_FILES})
        return PullRequest(
            repository=repo,
            number=number,
            title=pr["title"],
            state=pr["state"],
            author=pr["user"]["login"],
            merged=bool(pr.get("merged")),
            merge_commit_sha=pr.get("merge_commit_sha"),
            created_at=pr["created_at"],
            merged_at=pr.get("merged_at"),
            body=(pr.get("body") or "")[:5000],
            files=[_gh_file(f) for f in files],
        )

    async def commit_diff(self, repo: str, sha: str) -> CommitDiff:
        c = await self._get(f"/repos/{repo}/commits/{validate_sha(sha)}")
        return CommitDiff(
            repository=repo,
            sha=c["sha"],
            author=c["commit"]["author"]["name"],
            date=c["commit"]["author"]["date"],
            message=c["commit"]["message"][:2000],
            files=[_gh_file(f) for f in c.get("files", [])[:MAX_DIFF_FILES]],
        )


def _gh_file(f: dict[str, Any]) -> FileChange:
    patch, truncated = _truncate_patch(f.get("patch"))
    return FileChange(
        path=f["filename"],
        status=f["status"],
        additions=f.get("additions", 0),
        deletions=f.get("deletions", 0),
        patch=patch,
        patch_truncated=truncated,
    )


@dataclass
class LocalGitProvider:
    root: Path
    timeout: float = 10.0

    async def _git(self, *args: str) -> str:
        if not (self.root / ".git").exists():
            raise UpstreamUnavailableError("Local repository is not available")
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.quotepath=off",
            "--no-pager",
            *args,
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent", "PATH": "/usr/bin:/bin:/usr/local/bin"},
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError as exc:
            proc.kill()
            raise UpstreamUnavailableError("git timed out") from exc
        if proc.returncode not in (0, 1):  # git grep returns 1 for "no matches"
            msg = err.decode(errors="replace")
            if "unknown revision" in msg or "does not exist" in msg or "bad object" in msg or "invalid object" in msg:
                raise NotFoundError("Ref, path or commit not found in repository")
            raise UpstreamUnavailableError("git command failed")
        return out.decode("utf-8", errors="replace")

    async def search_code(self, repo: str, query: str, limit: int) -> list[CodeMatch]:
        q = validate_search_query(query)
        out = await self._git("grep", "-n", "-I", "--fixed-strings", "-e", q, "HEAD", "--")
        matches = []
        for line in out.splitlines()[:limit]:
            _, path, lineno, text = line.split(":", 3)
            matches.append(CodeMatch(path=path, line=int(lineno), snippet=text.strip()[:300]))
        return matches

    async def get_file(self, repo: str, path: str, ref: str) -> FileContent:
        content = await self._git("show", f"{validate_ref(ref)}:{validate_path(path)}")
        raw = content.encode()
        return FileContent(
            repository=repo,
            path=path,
            ref=ref,
            size_bytes=len(raw),
            truncated=len(raw) > MAX_FILE_BYTES,
            content=raw[:MAX_FILE_BYTES].decode(errors="replace"),
        )

    async def recent_commits(self, repo: str, ref: str, limit: int, path: str | None) -> list[CommitSummary]:
        args = ["log", f"-n{limit}", "--format=%H%x1f%an%x1f%aI%x1f%B%x1e", validate_ref(ref), "--"]
        if path:
            args.append(validate_path(path))
        out = await self._git(*args)
        commits = []
        for rec in out.split("\x1e"):
            parts = rec.strip().split("\x1f")
            if len(parts) == 4:
                commits.append(
                    CommitSummary(sha=parts[0], author=parts[1], date=parts[2], message=parts[3].strip()[:2000])
                )
        return commits

    async def pull_request(self, repo: str, number: int) -> PullRequest:
        raise ValidationFailedError("Pull requests are not available for local repositories; use commit tools")

    async def commit_diff(self, repo: str, sha: str) -> CommitDiff:
        sha = validate_sha(sha)
        header = await self._git("show", "-s", "--format=%H%x1f%an%x1f%aI%x1f%B", sha)
        full, author, date, message = header.split("\x1f", 3)
        numstat = await self._git("show", "--format=", "--numstat", sha)
        files = []
        for line in numstat.strip().splitlines()[:MAX_DIFF_FILES]:
            adds, dels, path = line.split("\t", 2)
            patch, truncated = _truncate_patch(await self._git("show", "--format=", "--patch", sha, "--", path))
            files.append(
                FileChange(
                    path=path,
                    status="modified",
                    additions=int(adds) if adds.isdigit() else 0,
                    deletions=int(dels) if dels.isdigit() else 0,
                    patch=patch,
                    patch_truncated=truncated,
                )
            )
        return CommitDiff(
            repository=repo, sha=full, author=author, date=date, message=message.strip()[:2000], files=files
        )


def valid_repo_name(name: str) -> bool:
    return bool(_REPO.match(name))
