#!/usr/bin/env python3
"""Backup Android phone via USB (ADB) to a local archive.

Archives images, downloads, media, and contacts (vCard via content query).
Never deletes from backup—preserves files even after they are removed from
the phone. Excludes Dropbox.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

DEFAULT_ARCHIVE_ROOT = Path.home() / "phone-archive"
DEFAULT_STORAGE_ROOT = "/storage/emulated/0"

# Paths to back up (relative to storage root). Dropbox is excluded.
BACKUP_PATHS = [
    "DCIM",       # Camera photos and videos
    "Pictures",   # Screenshots, saved images
    "Download",   # Downloads folder
    "Movies",     # Video files
    "Documents",  # Saved documents, PDFs
    "Recordings", # Voice memos, audio recordings
    "Alarms",     # Alarm sounds
    "Notifications",  # Notification sounds
    "Ringtones",  # Ringtone files
    "Podcasts",   # Downloaded podcasts
    "Audiobooks", # Audiobook files
]

# Paths containing these substrings are excluded (case-insensitive)
EXCLUDE_SUBSTRINGS = ["dropbox", ".dropbox"]

# Contacts (via adb shell content query — no root). Stored under archive root.
CONTACTS_SUBDIR = "ContactsBackup"
CONTACTS_VCF_NAME = "contacts.vcf"
CONTACTS_DATA_URI = "content://com.android.contacts/data"
# Colon-separated column list (Android `content query` syntax).
CONTACTS_PROJECTION = (
    "contact_id:mimetype:data1:data2:data3:display_name"
)
MIMETYPE_NAME = "vnd.android.cursor.item/name"
MIMETYPE_PHONE = "vnd.android.cursor.item/phone_v2"
MIMETYPE_EMAIL = "vnd.android.cursor.item/email_v2"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def run_adb(
    args: list[str],
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run adb command. Raises FileNotFoundError if adb not installed."""
    try:
        return subprocess.run(
            ["adb"] + args,
            capture_output=capture,
            text=True,
            check=check,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "adb not found. Install with: sudo apt install android-tools-adb"
        ) from None


def device_connected() -> bool:
    """Return True if exactly one Android device is connected."""
    result = run_adb(["devices"], check=True)
    lines = [l for l in result.stdout.strip().split("\n") if l and "List" not in l]
    devices = [l for l in lines if "device" in l and "unauthorized" not in l]
    return len(devices) == 1


def should_exclude(remote_path: str) -> bool:
    """Return True if path should be excluded (e.g. Dropbox)."""
    lower = remote_path.lower()
    return any(ex in lower for ex in EXCLUDE_SUBSTRINGS)


def get_storage_root() -> str:
    """Resolve storage root; /sdcard often links to emulated/0."""
    result = run_adb(["shell", "echo $EXTERNAL_STORAGE"], check=False)
    root = (result.stdout or "").strip()
    return root or DEFAULT_STORAGE_ROOT


def get_device_serial() -> str:
    """Return the connected device serial or a fallback label."""
    result = run_adb(["devices"], check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return "unknown-device"


def sanitize_phone_name(value: str) -> str:
    """Convert a string to a filesystem-safe slug."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "android-phone"


def get_phone_name() -> str:
    """Build a unique phone name from manufacturer, model, and serial."""
    manufacturer_result = run_adb(["shell", "getprop", "ro.product.manufacturer"], check=False)
    model_result = run_adb(["shell", "getprop", "ro.product.model"], check=False)
    manufacturer = (manufacturer_result.stdout or "").strip()
    model = (model_result.stdout or "").strip()
    serial = get_device_serial()

    base_name = "-".join(part for part in [manufacturer, model] if part)
    if not base_name:
        base_name = "android-phone"
    unique_name = f"{base_name}-{serial}"
    return sanitize_phone_name(unique_name)


def build_archive_path(archive_base: Path, phone_name: str) -> Path:
    """Return dated archive path: <base>/<phone-name>/YYYYMMDD."""
    run_date = datetime.now().strftime("%Y%m%d")
    return archive_base / phone_name / run_date


def parse_content_query_row(line: str) -> dict[str, str] | None:
    """Parse one line from `adb shell content query` output."""
    line = line.strip()
    if not line.startswith("Row:"):
        return None
    parts = line.split(None, 2)
    if len(parts) < 3:
        return None
    payload = parts[2]
    segments = re.split(r", (?=[a-z_][a-z0-9_]*=)", payload)
    out: dict[str, str] = {}
    for seg in segments:
        if "=" not in seg:
            continue
        key, _, val = seg.partition("=")
        key = key.strip()
        out[key] = val.strip()
    return out if out else None


def vcard_escape(value: str) -> str:
    """Escape special characters for vCard 3.0 property values."""
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def phone_type_label(data2: str) -> str:
    """Map ContactsContract phone type (data2) to a vCard TYPE token."""
    mapping = {
        "1": "HOME",
        "2": "MOBILE",
        "3": "WORK",
        "4": "WORK_FAX",
        "5": "HOME_FAX",
        "6": "PAGER",
        "7": "OTHER",
        "8": "CALLBACK",
        "9": "CAR",
        "10": "COMPANY_MAIN",
        "11": "ISDN",
        "12": "MAIN",
        "13": "OTHER_FAX",
        "14": "RADIO",
        "15": "TELEX",
        "16": "TTY_TDD",
        "17": "WORK_MOBILE",
        "18": "WORK_PAGER",
        "19": "ASSISTANT",
        "20": "MMS",
    }
    return mapping.get(data2.strip(), "VOICE")


def rows_to_vcard(rows: list[dict[str, str]]) -> str:
    """Build a vCard 3.0 document from content query rows."""
    by_contact: dict[str, dict[str, list | str | None]] = defaultdict(
        lambda: {
            "names": [],
            "phones": [],
            "emails": [],
            "display_name": None,
        }
    )
    for row in rows:
        cid = row.get("contact_id", "").strip()
        mime = row.get("mimetype", "").strip()
        if not cid or not mime:
            continue
        bucket = by_contact[cid]
        dn = row.get("display_name")
        if dn and dn != "NULL":
            bucket["display_name"] = dn
        data1 = row.get("data1") or ""
        if data1 == "NULL":
            data1 = ""
        data2 = row.get("data2") or ""
        if data2 == "NULL":
            data2 = ""
        data3 = row.get("data3") or ""
        if data3 == "NULL":
            data3 = ""
        if mime == MIMETYPE_NAME:
            bucket["names"].append((data1, data2, data3))
        elif mime == MIMETYPE_PHONE:
            if data1:
                bucket["phones"].append((data1, data2))
        elif mime == MIMETYPE_EMAIL:
            if data1:
                bucket["emails"].append(data1)

    lines: list[str] = []
    for cid in sorted(by_contact.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        data = by_contact[cid]
        names: list = data["names"]
        phones = data["phones"]
        emails = data["emails"]
        display_name = data["display_name"]
        given, family = "", ""
        fn = ""
        if display_name and display_name != "NULL":
            fn = display_name.strip()
        if names:
            full, given, family = names[0]
            if not fn:
                fn = (full or "").strip() or " ".join(
                    p for p in (given, family) if p
                ).strip()
        if not fn and not phones and not emails:
            continue
        if not fn:
            fn = "Unknown"
        if names:
            _full, given, family = names[0]
            n_field = ";".join(
                vcard_escape(x) for x in (family, given, "", "", "")
            )
        else:
            n_field = ";;;;"
        lines.append("BEGIN:VCARD")
        lines.append("VERSION:3.0")
        lines.append(f"FN:{vcard_escape(fn)}")
        lines.append(f"N:{n_field}")
        lines.append(f"X-ANDROID-CONTACT-ID:{vcard_escape(cid)}")
        for number, ptype in phones:
            label = phone_type_label(ptype)
            lines.append(f"TEL;TYPE={label}:{vcard_escape(number)}")
        for email in sorted(set(emails)):
            lines.append(f"EMAIL;TYPE=INTERNET:{vcard_escape(email)}")
        lines.append("END:VCARD")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def query_contact_rows(log: logging.Logger) -> tuple[list[dict[str, str]], bool]:
    """Fetch contact rows via adb content query. Returns (rows, query_ok)."""
    result = run_adb(
        [
            "shell",
            "content",
            "query",
            "--uri",
            CONTACTS_DATA_URI,
            "--projection",
            CONTACTS_PROJECTION,
        ],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        log.error("Contacts query failed (adb content query): %s", err or "unknown error")
        return [], False
    text = result.stdout or ""
    if "Error while accessing provider" in text:
        log.error("Contacts provider error: %s", text[:500])
        return [], False
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        parsed = parse_content_query_row(line)
        if parsed:
            rows.append(parsed)
    return rows, True


def backup_contacts(
    archive_root: Path,
    log: logging.Logger,
    dry_run: bool = False,
    add_only: bool = False,
) -> tuple[bool, bool]:
    """Export contacts to ContactsBackup/contacts.vcf. Returns (success, had_error)."""
    out_dir = archive_root / CONTACTS_SUBDIR
    out_file = out_dir / CONTACTS_VCF_NAME
    label = "[contacts]"

    if dry_run:
        log.info("%s Would export contacts to %s", label, out_file)
        return True, False

    if add_only and out_file.exists():
        log.info("%s Skip existing %s (add-only)", label, out_file)
        return True, False

    log.info("%s Querying device contacts...", label)
    rows, ok = query_contact_rows(log)
    if not ok:
        return False, True
    if not rows:
        log.warning("%s No contact rows returned; writing empty placeholder", label)
        vcf = (
            "# No contacts exported (empty or none visible to the contacts "
            "provider).\n"
        )
    else:
        vcf = rows_to_vcard(rows)
        log.info("%s Parsed %d data rows, building vCard", label, len(rows))

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(vcf, encoding="utf-8")
    log.info("%s Wrote %s", label, out_file)
    return True, False


# -----------------------------------------------------------------------------
# Backup Logic
# -----------------------------------------------------------------------------


def merge_add_only(
    src: Path,
    dst: Path,
    log: logging.Logger,
    show_progress: bool = True,
) -> int:
    """Copy files from src to dst only if they don't exist in dst. Returns count added."""
    files = [f for f in src.rglob("*") if f.is_file()]
    total = len(files)
    added = 0
    for i, f in enumerate(files, 1):
        rel = f.relative_to(src)
        target = dst / rel
        if target.exists():
            if show_progress and total > 20 and i % 100 == 0:
                log.info("  Scanning... %d/%d", i, total)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        added += 1
        if show_progress:
            log.info("  + %s", rel)
        else:
            log.debug("Added: %s", rel)
    return added


def pull_directory(
    remote_path: str,
    local_path: Path,
    log: logging.Logger,
    dry_run: bool = False,
    add_only: bool = False,
    progress: tuple[int, int] | None = None,
) -> tuple[bool, bool]:
    """Pull a directory from device to local. Returns (success, had_error)."""
    idx, total = progress or (1, 1)
    label = f"[{idx}/{total}]" if total > 1 else ""

    if dry_run:
        log.info("%s Would pull: %s -> %s", label, remote_path, local_path)
        return True, False

    if add_only:
        with tempfile.TemporaryDirectory(prefix="adb_backup_") as tmp:
            tmp_path = Path(tmp)
            log.info("%s Pulling %s from device...", label, Path(remote_path).name)
            result = run_adb(
                ["pull", "-a", remote_path, str(tmp_path)],
                check=False,
                capture=False,
            )
            if result.returncode != 0:
                err_msg = result.stderr or result.stdout or "unknown error"
                if "does not exist" in err_msg.lower():
                    log.warning("Path does not exist on device: %s", remote_path)
                    return False, False
                log.error("adb pull failed for %s: %s", remote_path, err_msg)
                return False, True
            pulled_dir = tmp_path / Path(remote_path).name
            if pulled_dir.exists():
                log.info("%s Merging new files into archive...", label)
                n = merge_add_only(pulled_dir, local_path, log)
                log.info("%s %s: %d new files added", label, Path(remote_path).name, n)
            else:
                log.info("%s %s: no files", label, Path(remote_path).name)
    else:
        local_path.mkdir(parents=True, exist_ok=True)
        log.info("%s Pulling %s...", label, Path(remote_path).name)
        result = run_adb(
            ["pull", "-a", remote_path, str(local_path)],
            check=False,
            capture=False,
        )
        if result.returncode != 0:
            err_msg = result.stderr or result.stdout or "unknown error"
            if "does not exist" in err_msg.lower():
                log.warning("Path does not exist on device: %s", remote_path)
                return False, False
            log.error("adb pull failed for %s: %s", remote_path, err_msg)
            return False, True
        log.info("%s %s done", label, Path(remote_path).name)

    return True, False


def backup(
    archive_root: Path,
    storage_root: str,
    log: logging.Logger,
    dry_run: bool = False,
    add_only: bool = False,
    backup_contacts_enabled: bool = True,
) -> int:
    """Run backup. Returns 0 on success, non-zero on failure."""
    if not device_connected():
        log.error("No Android device connected. Connect via USB and enable USB debugging.")
        return 1

    log.info("Archive: %s", archive_root.resolve())
    log.info("Device storage: %s", storage_root)

    ok_count = 0
    total_errors = 0

    if backup_contacts_enabled:
        _c_ok, c_err = backup_contacts(
            archive_root,
            log,
            dry_run=dry_run,
            add_only=add_only,
        )
        if c_err:
            total_errors += 1

    paths_to_backup = [p for p in BACKUP_PATHS if not should_exclude(f"{storage_root}/{p}")]
    total_dirs = len(paths_to_backup)
    current = 0

    for rel_path in BACKUP_PATHS:
        remote = f"{storage_root}/{rel_path}"
        if should_exclude(remote):
            log.info("Skipping (excluded): %s", remote)
            continue

        current += 1
        local = archive_root / rel_path
        ok, err = pull_directory(
            remote,
            local,
            log,
            dry_run=dry_run,
            add_only=add_only,
            progress=(current, total_dirs),
        )
        if ok:
            ok_count += 1
        if err:
            total_errors += 1

    # Post-backup summary
    file_count = 0
    total_bytes = 0
    for f in archive_root.rglob("*"):
        if f.is_file():
            file_count += 1
            total_bytes += f.stat().st_size
    size_mb = total_bytes / (1024 * 1024)
    log.info("Backup complete. Directories: %d, errors: %d", ok_count, total_errors)
    log.info("Archive total: %d files, %.1f MB in %s", file_count, size_mb, archive_root)
    return 0 if total_errors == 0 else 1


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Backup Android phone via USB (ADB) to a local archive.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_ARCHIVE_ROOT,
        help="Archive base directory",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Show what would be done without copying",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Write log to file (for Portainer/external review)",
    )
    parser.add_argument(
        "--add-only",
        action="store_true",
        default=True,
        help="Only add new files; never overwrite (default)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files in archive",
    )
    parser.add_argument(
        "--skip-contacts",
        action="store_true",
        help="Do not export contacts to ContactsBackup/contacts.vcf",
    )
    return parser.parse_args()


def setup_logging(args: argparse.Namespace) -> logging.Logger:
    """Configure logging."""
    level = logging.DEBUG if args.verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(args.log_file))

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    """Entry point."""
    args = parse_args()
    log = setup_logging(args)

    add_only = args.add_only and not args.overwrite
    try:
        storage_root = get_storage_root()
        phone_name = get_phone_name()
        archive_path = build_archive_path(args.output, phone_name)
        log.info("Phone name: %s", phone_name)
        return backup(
            archive_root=archive_path,
            storage_root=storage_root,
            log=log,
            dry_run=args.dry_run,
            add_only=add_only,
            backup_contacts_enabled=not args.skip_contacts,
        )
    except FileNotFoundError as e:
        log.error("%s", e)
        return 1
    except KeyboardInterrupt:
        log.info("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
