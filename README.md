# Android Phone Archive

Backs up an Android phone via USB (ADB) to a local archive. It copies standard user folders from shared storage, exports **contacts** as a vCard file, and uses a **per-device, per-day** folder layout. The tool **never deletes** anything from the archive, even after files are removed from the phone.

## Features

### Where backups go

- **Base directory**: `~/phone-archive` by default (`-o` / `--output` sets another base).
- **Device folder**: A stable slug from **manufacturer**, **model**, and **ADB serial** (e.g. `oneplus-le2117-eac7b419`), so each physical phone has its own subtree.
- **Run folder**: **`YYYYMMDD`** (local date when you run the script), e.g. `20260406`, so repeat runs on different days land in separate folders.

Full path pattern:

```text
<base>/<device-slug>/<YYYYMMDD>/
```

### Media and files (under shared storage, usually `/sdcard`)

Each item is mirrored as a folder under the run directory. Paths containing `dropbox` (case-insensitive) are skipped.

| Folder | Contents |
| --- | --- |
| **DCIM** | Camera photos and videos |
| **Pictures** | Screenshots, saved images |
| **Download** | Downloads |
| **Movies** | Videos |
| **Documents** | Documents, PDFs, etc. |
| **Recordings** | Voice memos, recordings |
| **Alarms** | Alarm sounds |
| **Notifications** | Notification sounds |
| **Ringtones** | Ringtones |
| **Podcasts** | Podcasts |
| **Audiobooks** | Audiobooks |

- **Add-only by default**: New files are merged in; existing files in the archive are **not** overwritten. Use **`--overwrite`** for a full sync-style replace on pulled trees.
- **Dropbox**: Any remote path whose name contains `dropbox` is excluded.

### Contacts backup

- **Output**: `ContactsBackup/contacts.vcf` (vCard 3.0) inside the same run folder as the media backup.
- **Mechanism**: `adb shell content query` on the Android contacts provider (`content://com.android.contacts/data`)—**no root**. Names, phone numbers (with type), and emails are included where the provider exposes them.
- **What is included**: Entries visible to the system **Contacts** provider (e.g. Google account, device storage, SIM contacts that appear in Contacts). Entries that never sync into Contacts may not appear.
- **Order**: Contacts are exported **first**, then media folders are pulled.
- **Add-only**: If `contacts.vcf` already exists for that run, the contact step is **skipped** unless you use **`--overwrite`** (same rule as other archive files).
- **Opt out**: **`--skip-contacts`** backs up only media folders.

### Other

- **Single device**: Expects **exactly one** authorized Android device on USB (`adb devices`).
- **Logging**: Optional **`--log-file`** for a persistent log (e.g. Docker/Portainer).
- **Verbose**: **`--verbose`** for debug-level messages.

## Prerequisites

1. **ADB** on your computer:

   ```bash
   sudo apt install android-tools-adb   # Debian/Ubuntu
   ```

2. **USB debugging** on your phone:

   - Settings → About phone → tap "Build number" 7 times
   - Settings → Developer options → enable "USB debugging"
   - Connect via USB and authorize the computer when prompted

3. **Ubuntu: Fix USB permissions** (if you see "insufficient permissions for device: missing udev rules"):

   Find your device's USB IDs (with phone connected):

   ```bash
   lsusb
   ```

   Look for your phone (e.g. `ID 18d1:4ee9 Google Inc. Nexus/Pixel Device`). Note the vendor ID (e.g. `18d1`) and product ID (e.g. `4ee9`).

   Add a udev rule:

   ```bash
   echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="4ee9", MODE="0666", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/51-android.rules
   ```

   Apply and reconnect:

   ```bash
   sudo udevadm control --reload-rules
   sudo udevadm trigger
   ```

   Unplug and reconnect the phone, then:

   ```bash
   adb kill-server
   adb start-server
   adb devices
   ```

   Replace `18d1` and `4ee9` with your device's vendor and product IDs from `lsusb`. Ensure your user is in the `plugdev` group: `groups` (if not: `sudo usermod -aG plugdev $USER`, then log out and back in).

## Usage

```bash
# Default: ~/phone-archive/<device-slug>/<YYYYMMDD>/ (add-only, includes contacts)
python backup_android.py

# Custom base directory (device slug and date folders are still appended)
python backup_android.py -o /path/to/archive

# Dry run (no copies; shows archive path and planned pulls)
python backup_android.py -n

# Overwrite existing files in the archive (media + contacts.vcf when re-run)
python backup_android.py --overwrite

# Media only (skip contacts export)
python backup_android.py --skip-contacts

# Log to file (e.g. for Docker/Portainer)
python backup_android.py --log-file ./logs/backup.log

# Verbose logging
python backup_android.py -v
```

## Archive layout

Example (names will match your device and run date):

```text
~/phone-archive/
└── oneplus-le2117-eac7b419/
    └── 20260406/
        ├── ContactsBackup/
        │   └── contacts.vcf    # vCard export from the contacts provider
        ├── DCIM/
        ├── Pictures/
        ├── Download/
        ├── Movies/
        ├── Documents/
        ├── Recordings/
        ├── Alarms/
        ├── Notifications/
        ├── Ringtones/
        ├── Podcasts/
        └── Audiobooks/
```

## Suggestions

- **Music**: Add `Music` to `BACKUP_PATHS` in `backup_android.py` if you want your music library (can be large).
- **WhatsApp media**: Usually under `Android/media/`; can be large and app-specific (not in the default paths).
- **Scheduled runs**: Use cron or a systemd timer for regular backups; each run uses the calendar date for the folder name.
- **Docker**: Mount the archive directory and log directory; run as a scheduled job.

## License

MIT
