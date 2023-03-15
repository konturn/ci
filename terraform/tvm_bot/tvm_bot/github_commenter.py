#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import re
import os
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Set

from .git_utils import GitHubRepo

BOT_COMMENT_START = "<!---bot-comment-->"
WELCOME_TEXT = (
    "Thanks for contributing to TVM! Please refer to the contributing "
    "guidelines https://tvm.apache.org/docs/contribute/ for useful information and tips. "
    "Please request code reviews from [Reviewers]("
    "https://github.com/apache/incubator-tvm/blob/master/CONTRIBUTORS.md#reviewers) by @-ing them in a comment."
)


@dataclass
class Item:
    key: str
    text: Optional[str]
    is_done: bool


class BotCommentBuilder:
    ALLOWLIST_USERS = {"driazati", "gigiblender", "areusch"}
    OPT_OUT_USERS: Set[str] = set()

    def __init__(
        self, github: GitHubRepo, data: Dict[str, Any], bot_login: str = "tvm-bot"
    ):
        self.github = github
        self.pr_number = data["number"]
        self.comment_data = data["comments"]["nodes"]
        self.author = data["author"]["login"]
        self.bot_login = bot_login
        self.log = logging.getLogger("py-github")

    def find_bot_comment(self) -> Optional[Dict[str, Any]]:
        """
        Return the existing bot comment or None if it does not exist
        """
        for comment in self.comment_data:
            self.log.info(f"Checking comment {comment}")
            if (
                comment["author"]["login"] == self.bot_login
                and BOT_COMMENT_START in comment["body"]
            ):
                self.log.info("Found existing comment")
                return comment
        self.log.info("No existing comment found")
        return None

    def is_done(self, key: str) -> bool:
        """
        Return True if the key has a marker that means it doesn't need to be
        re-generated anymore
        """
        body = self.find_existing_body()
        if key not in body:
            return False

        return body[key].is_done

    def find_existing_body(self) -> Dict[str, Item]:
        """
        Find existing dynamic bullet point items
        """
        existing_comment = self.find_bot_comment()
        if existing_comment is None:
            self.log.info("No existing comment while searching for body items")
            return {}

        matches = re.findall(
            r"<!--bot-comment-([a-z][a-z-]+)-start-->([\S\s]*?)<!--bot-comment-([a-z-]+)-end-->",
            existing_comment["body"],
            flags=re.MULTILINE,
        )
        self.log.info(f"Fetch body item matches: {matches}")

        items = {}
        for start, text, end in matches:
            if start != end:
                raise RuntimeError(
                    f"Malformed comment found: {start} marker did not have matching end, found instead {end}"
                )
            content = text.strip().lstrip("* ")
            is_done = self.done_key(start) in content
            items[start] = Item(key=start, text=content, is_done=is_done)

        self.log.info(f"Found body items: {items}")
        return items

    def _post_comment(self, body_items: Dict[str, Item]):
        comment = BOT_COMMENT_START + "\n\n" + WELCOME_TEXT + "\n\n"
        for key, item in body_items.items():
            content = item.text
            if content is None:
                continue
            line = self.start_key(key) + "\n * " + content.strip() + self.end_key(key)
            self.log.info(f"Adding line {line}")
            comment += line
        comment += (
            "\n\n<sub>Generated by [tvm-bot]("
            "https://github.com/apache/tvm/blob/main/ci/README.md#github-actions)</sub>"
        )

        data = {"body": comment}
        url = f"issues/{self.pr_number}/comments"

        self.log.info(f"Commenting {comment} on {url}")

        # if self.author not in self.ALLOWLIST_USERS:
        #     self.log.info(f"Skipping comment for author {self.author}")
        #     return

        if self.author in self.OPT_OUT_USERS:
            self.log.info(f"Skipping comment for opted-out author {self.author}")
            return

        if os.getenv("SKIP_COMMENT", "") == "1":
            return

        existing_comment = self.find_bot_comment()
        if existing_comment is None:
            # Comment does not exist, post it
            r = self.github.post(url, data)
        else:
            # Comment does exist, update it
            comment_url = f"issues/comments/{existing_comment['databaseId']}"
            r = self.github.patch(comment_url, data)

        self.log.info(f"Got response from posting comment: {r}")

    def start_key(self, key: str) -> str:
        return f"<!--bot-comment-{key}-start-->"

    def end_key(self, key: str) -> str:
        return f"<!--bot-comment-{key}-end-->"

    def done_key(self, key: str) -> str:
        return f"<!--bot-item-{key}-done-->"

    def post_items(self, items: List[Item]):
        """
        Update or post bullet points in the PR based on 'items' which is a
        list of (key, text, is_done) tuples
        """
        # Find the existing bullet points
        body_items = self.find_existing_body()

        # Add or update the requested items
        for item in items:
            if item.text is None or item.text.strip() == "":
                self.log.info(f"Skipping {item.key} since it was empty")
                continue
            self.log.info(f"Updating comment items {item.key} with {item.text}")
            body_items[item.key] = Item(
                key=item.key, text=item.text.strip(), is_done=item.is_done
            )

        # Post or update the comment
        # print(body_items)
        self._post_comment(body_items=body_items)
