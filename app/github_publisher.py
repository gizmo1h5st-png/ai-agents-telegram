import logging
import time
from typing import Dict, Optional

from github import Github, GithubException, InputGitTreeElement

from app.config import settings

logger = logging.getLogger(__name__)


class GitHubPublisherError(Exception):
    pass


class GitHubConflictError(GitHubPublisherError):
    pass


class GitHubPublisher:
    """Small GitHub API publisher based on PyGithub.

    Uses Git Data API to create one commit with multiple files.
    Never force-pushes.
    """

    def __init__(self):
        if not settings.GITHUB_TOKEN:
            raise GitHubPublisherError("GITHUB_TOKEN is not configured")
        if not settings.GITHUB_REPO:
            raise GitHubPublisherError("GITHUB_REPO is not configured")
        self.github = Github(settings.GITHUB_TOKEN)
        self.repo = self.github.get_repo(settings.GITHUB_REPO)

    def get_default_branch(self) -> str:
        return settings.GITHUB_BRANCH or self.repo.default_branch

    def get_or_create_branch(self, branch_name: str, base_branch: Optional[str] = None):
        base_branch = base_branch or self.get_default_branch()
        try:
            return self.repo.get_branch(branch_name)
        except GithubException as e:
            if e.status != 404:
                raise

        base_ref = self.repo.get_git_ref(f"heads/{base_branch}")
        self.repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_ref.object.sha)
        logger.info(f"GitHub branch created: repo={settings.GITHUB_REPO}, branch={branch_name}, base={base_branch}")
        return self.repo.get_branch(branch_name)

    def get_file_sha(self, path: str, branch: str) -> Optional[str]:
        try:
            content = self.repo.get_contents(path, ref=branch)
            if isinstance(content, list):
                return None
            return content.sha
        except GithubException as e:
            if e.status == 404:
                return None
            raise

    def _check_conflicts(self, files: Dict[str, str], expected_base_shas: Optional[Dict[str, Optional[str]]], branch: str):
        if not expected_base_shas:
            return
        for path in files:
            expected_sha = expected_base_shas.get(path)
            current_sha = self.get_file_sha(path, branch)
            if expected_sha is None and current_sha is not None:
                raise GitHubConflictError(f"File {path} already exists remotely")
            if expected_sha and current_sha != expected_sha:
                raise GitHubConflictError(f"File {path} changed remotely before push")

    def create_commit(
        self,
        files: Dict[str, str],
        message: str,
        branch: str,
        expected_base_shas: Optional[Dict[str, Optional[str]]] = None,
        retries: int = 3,
    ) -> str:
        if not files:
            raise GitHubPublisherError("No files to commit")

        for attempt in range(retries):
            try:
                self.get_or_create_branch(branch)
                self._check_conflicts(files, expected_base_shas, branch)

                ref = self.repo.get_git_ref(f"heads/{branch}")
                base_commit = self.repo.get_git_commit(ref.object.sha)
                base_tree = base_commit.tree

                elements = []
                for path, content in files.items():
                    blob = self.repo.create_git_blob(content, "utf-8")
                    elements.append(InputGitTreeElement(path=path, mode="100644", type="blob", sha=blob.sha))

                tree = self.repo.create_git_tree(elements, base_tree)
                commit = self.repo.create_git_commit(message=message, tree=tree, parents=[base_commit])

                # force=False is important: do not overwrite remote changes.
                ref.edit(commit.sha, force=False)
                url = f"https://github.com/{settings.GITHUB_REPO}/commit/{commit.sha}"
                logger.info(f"GitHub commit created: {url}")
                return url

            except GitHubConflictError:
                raise
            except GithubException as e:
                status = getattr(e, "status", None)
                logger.warning(f"GitHub commit attempt {attempt + 1}/{retries} failed: status={status}, data={str(getattr(e, 'data', ''))[:200]}")
                if status in (409, 422, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                if status == 401:
                    raise GitHubPublisherError("GitHub token is invalid or expired") from e
                if status == 403:
                    raise GitHubPublisherError("GitHub permission denied, rate limited, or branch protected") from e
                raise GitHubPublisherError(str(e)) from e

        raise GitHubPublisherError("GitHub commit failed after retries")

    def create_pull_request(self, branch: str, title: str, body: str = "") -> str:
        pr = self.repo.create_pull(title=title, body=body, head=branch, base=self.get_default_branch())
        return pr.html_url

