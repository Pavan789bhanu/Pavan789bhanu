#!/usr/bin/env python3
"""Sync featured project names and descriptions from GitHub into README.md."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "featured-projects.json"
README_PATH = ROOT / "README.md"
START_MARKER = "<!-- FEATURED_PROJECTS:START -->"
END_MARKER = "<!-- FEATURED_PROJECTS:END -->"


def api_request(url: str) -> dict | list | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-sync",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"GitHub API error for {url}: {exc.code} {exc.reason}", file=sys.stderr)
        return None


def fetch_readme(owner: str, repo: str) -> str:
    data = api_request(f"https://api.github.com/repos/{owner}/{repo}/readme")
    if not data or "content" not in data:
        return ""

    import base64

    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def strip_markup(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def humanize_repo_name(repo: str) -> str:
    parts = re.split(r"[-_]+", repo)
    return " ".join(part[:1].upper() + part[1:] if part else "" for part in parts)


def clean_title(title: str, repo: str) -> str:
    title = strip_markup(title)
    title = re.sub(
        r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F]+\s*",
        "",
        title,
    ).strip()

    if title.lower().replace(" ", "-").replace("_", "-") == repo.lower():
        return humanize_repo_name(repo)

    return title


def extract_title(readme: str, repo: str) -> str:
    html_match = re.search(r"<h1[^>]*>(.*?)</h1>", readme, re.IGNORECASE | re.DOTALL)
    if html_match:
        title = clean_title(html_match.group(1), repo)
        if title:
            return title

    for line in readme.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            title = clean_title(match.group(1), repo)
            if title and title.lower() not in {"readme", "license"}:
                return title

    return humanize_repo_name(repo)


def summarize_text(text: str, max_len: int = 240, max_sentences: int = 2) -> str:
    text = strip_markup(text)
    if not text:
        return ""

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    summary = " ".join(sentences[:max_sentences])
    if len(summary) > max_len:
        summary = summary[: max_len - 3].rstrip() + "..."
    return summary


def extract_description(readme: str) -> str:
    lines = readme.splitlines()

    # Blockquote summary near the top of the README.
    for index, line in enumerate(lines[:20]):
        if not line.strip().startswith(">"):
            continue
        quote_lines = []
        cursor = index
        while cursor < len(lines) and lines[cursor].strip().startswith(">"):
            quote_lines.append(lines[cursor].strip().lstrip(">").strip())
            cursor += 1
        quote = " ".join(quote_lines)
        if quote:
            return summarize_text(quote)

    # HTML summary block (e.g. centered <p><strong>...</strong><br/>...</p>).
    paragraph_match = re.search(r"<p[^>]*>(.*?)</p>", readme, re.IGNORECASE | re.DOTALL)
    if paragraph_match:
        inner = paragraph_match.group(1)
        inner = inner.replace("<br/>", " ").replace("<br>", " ")
        strong_match = re.search(r"<strong>(.*?)</strong>", inner, re.IGNORECASE | re.DOTALL)
        rest = strip_markup(re.sub(r"<strong>.*?</strong>", "", inner, flags=re.IGNORECASE | re.DOTALL))
        strong_text = strip_markup(strong_match.group(1)) if strong_match else ""
        if strong_text and rest and not rest.lower().startswith(strong_text.lower()):
            return summarize_text(f"{strong_text} — {rest}")
        if strong_text:
            return summarize_text(strong_text)
        if rest:
            return summarize_text(rest)

    # Markdown intro paragraph, including bold lead-ins like **Tagline.** Rest of paragraph.
    collecting = False
    paragraph_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not collecting:
            if stripped.startswith("#") or not stripped or stripped.startswith("<!--"):
                continue
            if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
                break
            collecting = True

        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("```") or stripped.startswith("|"):
            break
        if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            break
        paragraph_lines.append(stripped)

    if paragraph_lines:
        paragraph = " ".join(paragraph_lines)
        bold_match = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", paragraph)
        if bold_match:
            lead = strip_markup(bold_match.group(1))
            rest = strip_markup(bold_match.group(2))
            return summarize_text(f"{lead} {rest}".strip())
        return summarize_text(paragraph)

    # Subheading with inline description (common in technical READMEs).
    for line in lines:
        match = re.match(r"^#{2,6}\s+(.+)$", line.strip())
        if not match:
            continue
        text = strip_markup(match.group(1))
        if len(text) >= 60:
            return summarize_text(text)

    return ""


def fetch_project_metadata(owner: str, repo: str) -> tuple[str, str]:
    repo_data = api_request(f"https://api.github.com/repos/{owner}/{repo}") or {}
    readme = fetch_readme(owner, repo)

    name = extract_title(readme, repo)
    description = (repo_data.get("description") or "").strip()
    if not description:
        description = extract_description(readme)

    if not description:
        description = f"Explore {name} on GitHub."

    return name, description


def render_tags(tags: list[str]) -> str:
    return " ".join(f"`{tag}`" for tag in tags)


def render_project_card(owner: str, project: dict, name: str, description: str) -> str:
    repo = project["repo"]
    emoji = project.get("emoji", "")
    tags = render_tags(project.get("tags", []))
    highlights = "\n".join(f"- {item}" for item in project.get("highlights", []))
    title = f"{emoji} [{name}](https://github.com/{owner}/{repo})".strip()

    return (
        f"### {title}\n\n"
        f"> {description}\n\n"
        f"{tags}\n\n"
        f"{highlights}\n"
    )


def render_projects_table(owner: str, projects: list[dict]) -> str:
    cards: list[str] = []
    for project in projects:
        name, description = fetch_project_metadata(owner, project["repo"])
        cards.append(render_project_card(owner, project, name, description))

    rows: list[str] = ["<table>"]
    for index in range(0, len(cards), 2):
        rows.append("<tr>")
        rows.append('<td width="50%" valign="top">')
        rows.append("")
        rows.append(cards[index].rstrip())
        rows.append("")
        rows.append("</td>")

        if index + 1 < len(cards):
            rows.append('<td width="50%" valign="top">')
            rows.append("")
            rows.append(cards[index + 1].rstrip())
            rows.append("")
            rows.append("</td>")

        rows.append("</tr>")

    rows.append("</table>")
    return "\n".join(rows)


def replace_marked_section(readme: str, new_section: str) -> str:
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    replacement = f"{START_MARKER}\n{new_section}\n{END_MARKER}"
    if not pattern.search(readme):
        raise SystemExit(
            f"Could not find featured projects markers in {README_PATH}. "
            f"Expected {START_MARKER} and {END_MARKER}."
        )
    return pattern.sub(replacement, readme, count=1)


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    owner = config["owner"]
    projects = config["projects"]

    table = render_projects_table(owner, projects)
    readme = README_PATH.read_text(encoding="utf-8")
    updated = replace_marked_section(readme, table)
    README_PATH.write_text(updated, encoding="utf-8")

    print(f"Synced {len(projects)} featured projects into {README_PATH.name}")
    for project in projects:
        name, description = fetch_project_metadata(owner, project["repo"])
        print(f"- {project['repo']}: {name} — {description}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
