"""
Google File Manager - Standalone tool to list and delete files on Google File API.

This is a completely standalone script that does NOT modify the Ebook-Translator plugin.
It reads the Gemini API key from the plugin's config and lets you manage uploaded files.

Usage:
    python google_file_manager.py
"""

import json
import os
import sys
import ssl

# Fix Unicode output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore


# ─── Config ───────────────────────────────────────────────────────────────────

# Google Gemini API base URL
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


# ─── API Key Reader ───────────────────────────────────────────────────────────

def get_gemini_api_key():
    """Read the Gemini API key from the Ebook-Translator plugin config."""
    # Try multiple possible config locations
    possible_paths = [
        # Windows: Calibre portable / AppData
        os.path.expanduser("~\\AppData\\Roaming\\calibre\\plugins\\ebook_translator.json"),
        # Linux / macOS style
        os.path.expanduser("~/.config/calibre/plugins/ebook_translator.json"),
        # Calibre config dir
        os.path.join(os.environ.get("CALIBRE_CONFIG_DIR", ""), "plugins", "ebook_translator.json"),
    ]

    config = None
    for path in possible_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                break
            except (json.JSONDecodeError, IOError):
                continue

    if config is None:
        print("ERROR: Could not find Ebook-Translator config file.")
        print("Looked in:")
        for p in possible_paths:
            print(f"  - {p}")
        print("\nMake sure you have configured a Gemini API key in the plugin first.")
        sys.exit(1)

    # Navigate: engine_preferences -> Gemini -> api_keys -> [0]
    engine_prefs = config.get("engine_preferences", {})
    gemini_prefs = engine_prefs.get("Gemini", {})
    api_keys = gemini_prefs.get("api_keys", [])

    if not api_keys:
        print("ERROR: No API key found for Gemini engine.")
        print("Please configure your Gemini API key in the Ebook-Translator plugin first.")
        sys.exit(1)

    return api_keys[0]


# ─── HTTP Request (using urllib with proper SSL) ──────────────────────────────

def api_request(url, method="GET", data=None):
    """Make an HTTP request to the Google API using urllib with proper SSL."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    headers = {"Content-Type": "application/json"}

    # Encode data if provided
    body = None
    if data is not None:
        body = data.encode("utf-8") if isinstance(data, str) else data

    req = Request(url, data=body, headers=headers, method=method)

    # Create SSL context that doesn't verify (some corporate networks block this)
    # Try verified first, fall back to unverified
    try:
        ctx = ssl.create_default_context()
        with urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        if hasattr(e, "read"):
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                return {"error": error_body}
            except Exception:
                pass
        return {"error": {"message": str(e)}}
    except Exception as e:
        return {"error": {"message": str(e)}}


# ─── API Calls ────────────────────────────────────────────────────────────────

def list_files(api_key):
    """List ALL uploaded files on Google File API (handles pagination)."""
    all_files = []
    page_token = None
    page_num = 0
    while True:
        page_num += 1
        url = f"{BASE_URL}/files?key={api_key}&pageSize=100"
        if page_token:
            url += f"&pageToken={page_token}"
        print(f"  Fetching page {page_num}...", end=" ", flush=True)
        result = api_request(url)
        if "error" in result:
            print("ERROR")
            return result
        files = result.get("files", [])
        print(f"{len(files)} files")
        all_files.extend(files)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return {"files": all_files}


def delete_file(api_key, file_id):
    """Delete a single file by its file ID (e.g. 'files/abc123')."""
    url = f"{BASE_URL}/{file_id}?key={api_key}"
    return api_request(url, method="DELETE")


# ─── Display ──────────────────────────────────────────────────────────────────

def format_size(bytes_count):
    """Format byte count to human-readable string."""
    try:
        bytes_count = int(bytes_count)
    except (ValueError, TypeError):
        return str(bytes_count)
    if bytes_count >= 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    elif bytes_count >= 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count} B"


def format_time(iso_time):
    """Format ISO time string to a shorter readable format."""
    if not iso_time:
        return "N/A"
    # Take first 19 chars: "2026-05-02T18:30:00"
    return iso_time[:19].replace("T", " ")


def print_files_table(files):
    """Print files in a formatted table."""
    if not files:
        print("  No files found.")
        return

    print(f"\n  {'#':<4} {'Name':<30} {'Size':<10} {'State':<12} {'Created':<22} {'File ID'}")
    print(f"  {'-'*4} {'-'*30} {'-'*10} {'-'*12} {'-'*22} {'-'*50}")
    for i, f in enumerate(files, 1):
        name = f.get("displayName", "unnamed")[:28]
        size = format_size(f.get("sizeBytes", 0))
        state = f.get("state", "unknown")
        created = format_time(f.get("createTime", ""))
        file_id = f.get("name", "N/A")
        print(f"  {i:<4} {name:<30} {size:<10} {state:<12} {created:<22} {file_id}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  Google File Manager")
    print("  List and delete files uploaded to Google File API")
    print("=" * 70)

    # Get API key
    print("\n[1/3] Reading Gemini API key from plugin config...")
    api_key = get_gemini_api_key()
    print(f"  ✓ API key found: ...{api_key[-8:]}")

    # List files
    print("\n[2/3] Fetching uploaded files...")
    result = list_files(api_key)

    if "error" in result:
        print(f"  ✗ Error: {result['error'].get('message', str(result['error']))}")
        sys.exit(1)

    files = result.get("files", [])
    print(f"\n  ✓ Found {len(files)} file(s) total")
    print_files_table(files)

    if not files:
        print("\nNothing to delete. Exiting.")
        return

    # Delete files
    print("\n[3/3] Delete files")
    print("  Enter the numbers of files to delete (comma-separated, e.g. 1,3,5)")
    print("  Or type 'all' to delete ALL files")
    print("  Or press Enter to exit without deleting.")
    choice = input("  > ").strip().lower()

    if not choice:
        print("  No files deleted. Exiting.")
        return

    if choice == "all":
        confirm = input(f"  Delete ALL {len(files)} files? This cannot be undone! [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return
        indices = list(range(1, len(files) + 1))
    else:
        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
        except ValueError:
            print("  Invalid input. Exiting.")
            return

    for idx in indices:
        if idx < 1 or idx > len(files):
            print(f"  Skipping invalid index: {idx}")
            continue

        f = files[idx - 1]
        file_id = f.get("name")
        name = f.get("displayName", "unnamed")

        if choice != "all":
            confirm = input(f"  Delete '{name}' ({file_id})? [y/N]: ").strip().lower()
            if confirm != "y":
                print(f"  Skipped.")
                continue

        print(f"  Deleting {file_id}...", end=" ", flush=True)
        del_result = delete_file(api_key, file_id)
        if "error" in del_result:
            print(f"✗ {del_result['error'].get('message', str(del_result['error']))}")
        else:
            print("✓ Deleted!")

    print("\nDone!")


if __name__ == "__main__":
    main()
