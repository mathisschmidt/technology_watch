"""Writes and reads notes in the Obsidian vault folder."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

INBOX = "inbox"
VALIDATED = "validated"

_TITLE_RE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.M)


@dataclass
class VaultNote:
    path: Path
    title: str
    subfolder: str  # INBOX or VALIDATED


def list_notes(vault_folder: Path, subfolder: str) -> list[VaultNote]:
    """Return all notes in a vault subfolder, sorted newest-first."""
    directory = vault_folder / subfolder
    if not directory.exists():
        return []
    notes = []
    for md in sorted(directory.glob("*.md"), reverse=True):
        try:
            head = md.read_text(encoding="utf-8")[:1024]
        except OSError:
            continue
        m = _TITLE_RE.search(head)
        title = m.group(1).strip() if m else md.stem
        notes.append(VaultNote(path=md, title=title, subfolder=subfolder))
    return notes


def validate_note(vault_folder: Path, note_path: Path) -> Path:
    """Move a note from inbox/ to validated/. Returns the new path."""
    validated_dir = vault_folder / VALIDATED
    validated_dir.mkdir(parents=True, exist_ok=True)
    target = validated_dir / note_path.name
    if target.exists():
        stem, suffix = note_path.stem, note_path.suffix
        n = 2
        while True:
            candidate = validated_dir / f"{stem}-{n}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            n += 1
    note_path.rename(target)
    log.debug("Validated: moved %s → %s", note_path.name, target)
    return target


def write_note(vault_folder: Path, filename: str, body: str) -> Path:
    """Write a note to inbox/. On filename collision, append -2, -3, ..."""
    inbox = vault_folder / INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    (vault_folder / VALIDATED).mkdir(parents=True, exist_ok=True)
    target = inbox / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        n = 2
        while True:
            candidate = inbox / f"{stem}-{n}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            n += 1
        log.debug("Filename collision — writing to %s instead", target.name)
    log.debug("Writing note to %s (%d chars)", target, len(body))
    target.write_text(body, encoding="utf-8")
    return target
