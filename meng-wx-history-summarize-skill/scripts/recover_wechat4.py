from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import struct
from pathlib import Path

from Crypto.Cipher import AES


PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = 80
SQLITE_HEADER = b"SQLite format 3\x00"

DEFAULT_DB_DIR = os.environ.get("WECHAT_DB_STORAGE_DIR")
DEFAULT_WX_KEY_LOG = Path.home() / "AppData" / "Roaming" / "wx_key" / "app.log"
DEFAULT_KEYS_FILE = Path(".private") / "wechat_db_keys.json"
DEFAULT_DECRYPTED_DIR = Path("outputs") / "decrypted_databases"


def verify_enc_key(enc_key: bytes, page1: bytes) -> bool:
    salt = page1[:SALT_SIZE]
    mac_salt = bytes(value ^ 0x3A for value in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SIZE)
    hmac_data = page1[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    stored_hmac = page1[PAGE_SIZE - HMAC_SIZE : PAGE_SIZE]
    digest = hmac.new(mac_key, hmac_data, hashlib.sha512)
    digest.update(struct.pack("<I", 1))
    return hmac.compare_digest(digest.digest(), stored_hmac)


def derive_enc_key(passphrase: bytes, page1: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha512", passphrase, page1[:SALT_SIZE], 256_000, dklen=KEY_SIZE
    )


def find_passphrases(log_path: Path, explicit_hex: str | None) -> list[bytes]:
    candidates: list[bytes] = []
    seen: set[bytes] = set()

    def add_hex(value: str) -> None:
        try:
            raw = bytes.fromhex(value)
        except ValueError:
            return
        if len(raw) == KEY_SIZE and raw not in seen:
            seen.add(raw)
            candidates.append(raw)

    if explicit_hex:
        add_hex(explicit_hex.strip())

    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"\b[0-9a-fA-F]{64}\b", text):
            add_hex(match.group(0))

    return candidates


def collect_databases(db_dir: Path) -> list[Path]:
    result: list[Path] = []
    for path in db_dir.rglob("*.db"):
        lowered = path.name.lower()
        if lowered.endswith("-wal") or lowered.endswith("-shm"):
            continue
        if path.stat().st_size >= PAGE_SIZE:
            result.append(path)
    return sorted(result, key=lambda item: str(item).lower())


def select_passphrase(candidates: list[bytes], db_files: list[Path]) -> bytes:
    pages = []
    for db_path in db_files:
        with db_path.open("rb") as handle:
            page1 = handle.read(PAGE_SIZE)
        if len(page1) == PAGE_SIZE:
            pages.append((db_path, page1))

    for candidate in candidates:
        for db_path, page1 in pages:
            enc_key = derive_enc_key(candidate, page1)
            if verify_enc_key(enc_key, page1):
                print(
                    f"[OK] passphrase verified on {db_path.name}; "
                    f"prefix={candidate.hex()[:8]}..."
                )
                return candidate

    raise RuntimeError("no cached wx_key passphrase verified against current databases")


def build_key_map(passphrase: bytes, db_dir: Path, db_files: list[Path]) -> dict:
    output: dict[str, object] = {
        "_created_by": "auto_recover_wechat4.py",
        "_db_dir": "<local db dir redacted>",
        "_derivation": "PBKDF2-HMAC-SHA512(passphrase, db_salt, 256000, 32)",
    }

    ok = 0
    for db_path in db_files:
        with db_path.open("rb") as handle:
            page1 = handle.read(PAGE_SIZE)
        if len(page1) != PAGE_SIZE:
            continue

        enc_key = derive_enc_key(passphrase, page1)
        if not verify_enc_key(enc_key, page1):
            print(f"[WARN] derived key did not verify: {db_path}")
            continue

        rel = os.path.relpath(db_path, db_dir).replace("\\", "/")
        output[rel] = {
            "salt": page1[:SALT_SIZE].hex(),
            "enc_key": enc_key.hex(),
            "source": "cached_wx_key_passphrase",
        }
        ok += 1

    print(f"[OK] derived and verified {ok}/{len(db_files)} database keys")
    return output


def decrypt_page(enc_key: bytes, page_data: bytes, page_number: int) -> bytes:
    iv = page_data[PAGE_SIZE - RESERVE_SIZE : PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    if page_number == 1:
        encrypted = page_data[SALT_SIZE : PAGE_SIZE - RESERVE_SIZE]
        decrypted = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted)
        return SQLITE_HEADER + decrypted + (b"\x00" * RESERVE_SIZE)

    encrypted = page_data[: PAGE_SIZE - RESERVE_SIZE]
    decrypted = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(encrypted)
    return decrypted + (b"\x00" * RESERVE_SIZE)


def decrypt_database(db_path: Path, out_path: Path, enc_key: bytes) -> bool:
    file_size = db_path.stat().st_size
    total_pages = (file_size + PAGE_SIZE - 1) // PAGE_SIZE
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with db_path.open("rb") as source, out_path.open("wb") as target:
        for page_number in range(1, total_pages + 1):
            page = source.read(PAGE_SIZE)
            if not page:
                break
            if len(page) < PAGE_SIZE:
                page += b"\x00" * (PAGE_SIZE - len(page))
            target.write(decrypt_page(enc_key, page, page_number))

    try:
        conn = sqlite3.connect(out_path)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        conn.close()
    except sqlite3.DatabaseError as exc:
        print(f"[WARN] SQLite verification failed for {out_path}: {exc}")
        return False

    for suffix in ("-wal", "-shm"):
        residual = out_path.with_name(out_path.name + suffix)
        if residual.exists():
            residual.unlink()

    return True


def decrypt_all(db_dir: Path, out_dir: Path, key_map: dict) -> tuple[int, int]:
    success = 0
    failed = 0
    for rel, info in key_map.items():
        if rel.startswith("_") or not isinstance(info, dict):
            continue
        db_path = db_dir / rel
        if not db_path.exists():
            print(f"[WARN] source missing: {db_path}")
            failed += 1
            continue
        out_path = out_dir / rel
        ok = decrypt_database(db_path, out_path, bytes.fromhex(str(info["enc_key"])))
        if ok:
            print(f"[OK] decrypted {rel}")
            success += 1
        else:
            failed += 1
    return success, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover WeChat 4.1 database keys from cached wx_key logs and decrypt databases."
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path(DEFAULT_DB_DIR) if DEFAULT_DB_DIR else None,
        help="WeChat db_storage directory. Can also be set via WECHAT_DB_STORAGE_DIR.",
    )
    parser.add_argument("--wx-key-log", type=Path, default=DEFAULT_WX_KEY_LOG)
    parser.add_argument("--passphrase-hex")
    parser.add_argument("--keys-file", type=Path, default=DEFAULT_KEYS_FILE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DECRYPTED_DIR)
    parser.add_argument("--derive-only", action="store_true")
    args = parser.parse_args()

    db_dir = args.db_dir
    if db_dir is None:
        parser.error("--db-dir is required unless WECHAT_DB_STORAGE_DIR is set")
    if not db_dir.exists():
        raise FileNotFoundError(db_dir)

    db_files = collect_databases(db_dir)
    print(f"[INFO] found {len(db_files)} encrypted database files under {db_dir}")

    passphrases = find_passphrases(args.wx_key_log, args.passphrase_hex)
    print(f"[INFO] found {len(passphrases)} cached passphrase candidate(s)")
    if not passphrases:
        raise RuntimeError(f"no passphrase candidates found in {args.wx_key_log}")

    passphrase = select_passphrase(passphrases, db_files)
    key_map = build_key_map(passphrase, db_dir, db_files)
    args.keys_file.write_text(
        json.dumps(key_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] wrote key map: {args.keys_file.resolve()}")

    if args.derive_only:
        return

    success, failed = decrypt_all(db_dir, args.out_dir, key_map)
    print(f"[DONE] decrypted={success}, failed={failed}, out={args.out_dir.resolve()}")


if __name__ == "__main__":
    main()
