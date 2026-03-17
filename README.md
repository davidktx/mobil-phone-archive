# Android Phone Archive

Backs up an Android phone via USB (ADB) to a local archive. Preserves images, downloads, and media—**never deletes** from the archive, even after files are removed from the phone.

## Features

- **Images**: DCIM (camera), Pictures (screenshots, saved images)
- **Downloads**: Captures the Download folder
- **Movies**: Video files
- **Documents**: Saved documents, PDFs
- **Recordings**: Voice memos, audio recordings
- **Excludes Dropbox**: Skips any path containing "dropbox"
- **Add-only by default**: Never overwrites existing archive files
- **Log file support**: For Portainer or external review

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
# Default: backup to ~/phone-archive (add-only, no overwrite)
python backup_android.py

# Custom output directory
python backup_android.py -o /path/to/archive

# Dry run (show what would be done)
python backup_android.py -n

# Overwrite existing files (sync mode)
python backup_android.py --overwrite

# Log to file (e.g. for Docker/Portainer)
python backup_android.py --log-file ./logs/backup.log

# Verbose
python backup_android.py -v
```

## Archive Layout

```
~/phone-archive/
├── DCIM/          # Camera photos and videos
├── Pictures/      # Screenshots, saved images
├── Download/      # Downloaded files
├── Movies/        # Video files
├── Documents/     # Saved documents, PDFs
└── Recordings/    # Voice memos, audio recordings
```

## Suggestions

- **Music**: Add `Music` to `BACKUP_PATHS` in the script if you want to archive your music library (can be large)
- **WhatsApp media**: Usually under `Android/media/`; can be large and app-specific
- **Scheduled runs**: Use cron or systemd timer for regular backups
- **Docker**: Mount the archive directory and log directory; run as a scheduled job

## License

MIT
