"""Read current-head CI and paginated review evidence using the native GitHub CLI."""

import hashlib
import json
from pathlib import Path
import subprocess

from app.external_retry import run_external


class PullRequestReader:
    def __init__(self, repository: Path):
        self.repository = repository

    def _gh(self, args, payload=None):
        result = subprocess.run(
            ["gh", *args],
            cwd=self.repository,
            input=json.dumps(payload) if payload else None,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        result = json.loads(result.stdout)
        if isinstance(result, dict) and result.get("errors"):
            raise ValueError(json.dumps(result["errors"]))
        return result

    def _connection(self, node_id, node_type, selection, path):
        cursor = None
        nodes = []
        while True:
            query = (
                "query($id:ID!,$cursor:String){node(id:$id){... on "
                + node_type
                + "{"
                + selection
                + "}}}"
            )
            result = self._gh(
                ["api", "graphql", "--input", "-"],
                {"query": query, "variables": {"id": node_id, "cursor": cursor}},
            )
            connection = result["data"]["node"]
            for key in path:
                if connection is None:
                    return []
                connection = connection[key]
            nodes.extend(connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = connection["pageInfo"]["endCursor"]

    def __call__(self, url):
        return run_external("repair_pr_feedback_readback", lambda: self._read(url))

    def _read(self, url):
        pr = self._gh(
            [
                "pr",
                "view",
                url,
                "--json",
                "id,url,headRefName,headRefOid,state,reviewDecision",
            ]
        )
        page = "pageInfo{hasNextPage endCursor}"
        commit = self._gh(
            ["api", "graphql", "--input", "-"],
            {
                "query": "query($id:ID!){node(id:$id){... on PullRequest{commits(last:1){nodes{commit{id}}}}}}",
                "variables": {"id": pr["id"]},
            },
        )["data"]["node"]["commits"]["nodes"][0]["commit"]["id"]
        pr["checks"] = self._connection(
            commit,
            "Commit",
            "statusCheckRollup{contexts(first:100,after:$cursor){"
            + page
            + """ nodes{
                __typename ... on CheckRun{id name status conclusion detailsUrl}
                ... on StatusContext{id context state targetUrl}
            }}}""",
            ["statusCheckRollup", "contexts"],
        )
        pr["threads"] = self._connection(
            pr["id"],
            "PullRequest",
            "reviewThreads(first:100,after:$cursor){"
            + page
            + """ nodes{
                id isResolved isOutdated path line
            }}""",
            ["reviewThreads"],
        )
        for thread in pr["threads"]:
            thread["comments"] = (
                []
                if thread["isResolved"]
                else self._connection(
                    thread["id"],
                    "PullRequestReviewThread",
                    "comments(first:100,after:$cursor){"
                    + page
                    + """ nodes{
                    id body url updatedAt author{login}
                }}""",
                    ["comments"],
                )
            )
        for field, key in [("reviews", "reviews"), ("latestReviews", "latest_reviews")]:
            pr[key] = self._connection(
                pr["id"],
                "PullRequest",
                field
                + "(first:100,after:$cursor){"
                + page
                + """ nodes{
                    id state body url submittedAt updatedAt author{login} commit{oid}
                }}""",
                [field],
            )
        # A push between connection reads must not attribute evidence to the wrong head.
        latest = self._gh(["pr", "view", url, "--json", "headRefOid"])
        if latest["headRefOid"] != pr["headRefOid"]:
            raise ValueError("PR head changed during feedback readback")
        return pr


def review_fingerprint(review):
    return hashlib.sha256(json.dumps(review, sort_keys=True).encode()).hexdigest()


def assess_feedback(pr, handled_reviews=()):
    failures, pending = [], []
    for check in pr["checks"]:
        if check["__typename"] == "CheckRun":
            if check["status"] != "COMPLETED":
                pending.append(check)
            elif check["conclusion"] not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                failures.append(check)
        elif check["state"] == "PENDING":
            pending.append(check)
        elif check["state"] != "SUCCESS":
            failures.append(check)
    threads = [thread for thread in pr["threads"] if not thread["isResolved"]]
    reviews = [
        review
        for review in pr["latest_reviews"]
        if review["state"] == "CHANGES_REQUESTED"
    ]
    comments = [
        review
        for review in pr["reviews"]
        if review["state"] == "COMMENTED"
        and review["body"].strip()
        and review_fingerprint(review) not in handled_reviews
    ]
    feedback = {
        "head": pr["headRefOid"],
        "failed_checks": sorted(failures, key=lambda x: x["id"]),
        "unresolved_threads": sorted(threads, key=lambda x: x["id"]),
        "change_requests": sorted(reviews, key=lambda x: x["id"]),
        "commented_reviews": sorted(comments, key=lambda x: x["id"]),
    }
    identity = hashlib.sha256(json.dumps(feedback, sort_keys=True).encode()).hexdigest()
    actionable = bool(failures or threads or reviews or comments)
    passed = bool(pr["checks"]) and not (
        actionable or pending or pr["reviewDecision"] == "CHANGES_REQUESTED"
    )
    return feedback, identity, actionable, passed, len(pending)
