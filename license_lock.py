# -*- coding: utf-8 -*-
"""상용 라이선스 잠금: 메인보드 시리얼 + CPU ID(HWID)에 기기 1대만 허용."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

APP_NAME = "AutoShortsMaker"
REG_PATH = r"Software\AutoShortsMaker"
REG_VALUE = "Activation"
LOCK_DIR = Path.home() / ".auto_shorts_maker"
LOCK_FILE = LOCK_DIR / "license.lock"


def _pepper():
    seed = bytes([65, 117, 116, 111, 83, 104, 111, 114, 116, 115, 76, 111, 99, 107, 50, 48, 50, 54])
    extra = (os.getenv("LICENSE_MASTER_SECRET") or "").encode("utf-8")
    return hashlib.sha256(seed + extra + b"|asm-license-v1").digest()


def _ps_text(command):
    # type: (str) -> str
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if completed.returncode != 0:
            return ""
        return (completed.stdout or "").strip()
    except Exception:
        return ""


def _clean_id(value):
    # type: (str) -> str
    text = (value or "").strip().splitlines()[0].strip() if value else ""
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"none", "null", "to be filled by o.e.m.", "default string", "system serial number"}:
        return ""
    return text


def _hardware_parts():
    board = ""
    product_uuid = ""
    cpu = ""
    if os.name == "nt":
        board = _clean_id(_ps_text("(Get-CimInstance Win32_BaseBoard).SerialNumber"))
        product_uuid = _clean_id(_ps_text("(Get-CimInstance Win32_ComputerSystemProduct).UUID"))
        cpu = _clean_id(_ps_text("(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId"))
        if not board:
            board = _clean_id(_ps_text("(Get-CimInstance Win32_BIOS).SerialNumber"))
    if not cpu:
        cpu = _clean_id(os.getenv("PROCESSOR_IDENTIFIER") or "")
    if not board and not product_uuid and not cpu:
        import uuid
        import platform

        board = platform.node()
        cpu = str(uuid.getnode())
    return board, product_uuid, cpu


def get_hwid():
    # type: () -> str
    board, product_uuid, cpu = _hardware_parts()
    raw = "|".join([board, product_uuid, cpu]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().upper()


def machine_code():
    # type: () -> str
    hwid = get_hwid()
    return "-".join(hwid[i : i + 4] for i in range(0, 16, 4))


def formatted_hwid():
    # type: () -> str
    hwid = get_hwid()
    return "-".join(hwid[i : i + 4] for i in range(0, len(hwid), 4))


def normalize_key(key):
    # type: (str) -> str
    chars = [ch.upper() for ch in (key or "") if ch.isalnum()]
    if len(chars) < 12:
        return "".join(chars)
    body = "".join(chars)
    if body.startswith("ASM"):
        body = body[3:]
    body = body[:16]
    return "ASM-" + "-".join(body[i : i + 4] for i in range(0, len(body), 4))


def issue_key_for_hwid(hwid):
    # type: (str) -> str
    digest = hmac.new(_pepper(), ("MBCPU|" + hwid).encode("utf-8"), hashlib.sha256).hexdigest().upper()
    body = digest[:16]
    return "ASM-" + "-".join(body[i : i + 4] for i in range(0, 16, 4))


def issue_key_for_mobile_hwid(hwid):
    # type: (str) -> str
    digest = hmac.new(_pepper(), ("MOBILE|" + hwid).encode("utf-8"), hashlib.sha256).hexdigest().upper()
    body = digest[:16]
    return "ASM-" + "-".join(body[i : i + 4] for i in range(0, 16, 4))


def mobile_hwid(device_id, platform=""):
    # type: (str, str) -> str
    device = "".join((device_id or "").split())
    plat = (platform or "").strip().lower()
    if plat in ("iphone", "ipad", "apple"):
        plat = "ios"
    if plat in ("android", "aos"):
        plat = "android"
    raw = "MOBILE-DEVICE|{}|{}".format(plat, device)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()


def issue_key_for_mobile_device(device_id, platform=""):
    return issue_key_for_mobile_hwid(mobile_hwid(device_id, platform))


def issue_key_for_this_pc():
    return issue_key_for_hwid(get_hwid())


def _key_matches_hwid(key, hwid):
    expected = issue_key_for_hwid(hwid)
    return hmac.compare_digest(normalize_key(key), expected)


def _sign_payload(payload):
    body = json.dumps(
        {k: payload[k] for k in ("hwid", "key_fp", "activated_at") if k in payload},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hmac.new(_pepper(), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _fingerprint_key(key):
    return hashlib.sha256(normalize_key(key).encode("utf-8")).hexdigest()


def _read_registry():
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as handle:
            raw, _typ = winreg.QueryValueEx(handle, REG_VALUE)
        return json.loads(raw)
    except Exception:
        return None


def _write_registry(payload):
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as handle:
            winreg.SetValueEx(handle, REG_VALUE, 0, winreg.REG_SZ, json.dumps(payload, ensure_ascii=True))
    except Exception:
        pass


def _read_file():
    try:
        if not LOCK_FILE.is_file():
            return None
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_file(payload):
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(LOCK_FILE), 0x02)
    except Exception:
        pass


def load_activation():
    # type: () -> Optional[dict]
    for payload in (_read_file(), _read_registry()):
        if not payload or not isinstance(payload, dict):
            continue
        expected = _sign_payload(payload)
        if not hmac.compare_digest(str(payload.get("sig") or ""), expected):
            continue
        return payload
    return None


def save_activation(key):
    payload = {
        "hwid": get_hwid(),
        "key_fp": _fingerprint_key(key),
        "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload["sig"] = _sign_payload(payload)
    _write_file(payload)
    _write_registry(payload)
    return payload


def verify_saved_license():
    # type: () -> Tuple[bool, str]
    current = get_hwid()
    payload = load_activation()
    if not payload:
        return False, "라이선스가 등록되어 있지 않습니다."
    if str(payload.get("hwid") or "") != current:
        return False, "이 프로그램은 최초 등록된 1대의 컴퓨터에서만 실행할 수 있습니다."
    return True, "OK"


def activate_license(key):
    # type: (str) -> Tuple[bool, str]
    current = get_hwid()
    normalized = normalize_key(key)
    if not _key_matches_hwid(normalized, current):
        return False, "잘못된 라이선스 키이거나 이 컴퓨터용 키가 아닙니다."

    payload = load_activation()
    if payload:
        if str(payload.get("hwid") or "") != current:
            return False, "이 프로그램은 최초 등록된 1대의 컴퓨터에서만 실행할 수 있습니다."
        if str(payload.get("key_fp") or "") not in ("", _fingerprint_key(normalized)):
            return False, "이미 다른 라이선스 키로 이 기기가 등록되어 있습니다."
        return True, "이미 이 컴퓨터에 라이선스가 등록되어 있습니다."

    save_activation(normalized)
    return True, "라이선스가 이 컴퓨터에 등록되었습니다."


MOBILE_LOCK_FILE = LOCK_DIR / "license_mobile.lock"
REG_VALUE_MOBILE = "ActivationMobile"


def _read_mobile_file():
    try:
        if not MOBILE_LOCK_FILE.is_file():
            return None
        return json.loads(MOBILE_LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_mobile_file(payload):
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        MOBILE_LOCK_FILE.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(MOBILE_LOCK_FILE), 0x02)
    except Exception:
        pass


def _read_mobile_registry():
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as handle:
            raw, _typ = winreg.QueryValueEx(handle, REG_VALUE_MOBILE)
        return json.loads(raw)
    except Exception:
        return None


def _write_mobile_registry(payload):
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH) as handle:
            winreg.SetValueEx(
                handle, REG_VALUE_MOBILE, 0, winreg.REG_SZ, json.dumps(payload, ensure_ascii=True)
            )
    except Exception:
        pass


def load_mobile_activation():
    for payload in (_read_mobile_file(), _read_mobile_registry()):
        if not payload or not isinstance(payload, dict):
            continue
        expected = _sign_payload(payload)
        if not hmac.compare_digest(str(payload.get("sig") or ""), expected):
            continue
        return payload
    return None


def save_mobile_activation(key, hwid):
    payload = {
        "hwid": hwid,
        "key_fp": _fingerprint_key(key),
        "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload["sig"] = _sign_payload(payload)
    _write_mobile_file(payload)
    _write_mobile_registry(payload)
    return payload


def verify_mobile_license(device_id, platform=""):
    # type: (str, str) -> Tuple[bool, str]
    device = "".join((device_id or "").split())
    if not device:
        return False, "기기 번호(Android ID / iOS Vendor ID)가 없습니다."
    current = mobile_hwid(device, platform)
    payload = load_mobile_activation()
    if not payload:
        return False, "모바일 라이선스가 등록되어 있지 않습니다."
    if str(payload.get("hwid") or "") != current:
        return False, "이 라이선스는 최초 등록된 1대의 스마트폰에서만 사용할 수 있습니다."
    return True, "OK"


def activate_mobile_license(license_key, device_id, platform=""):
    # type: (str, str, str) -> Tuple[bool, str]
    device = "".join((device_id or "").split())
    if not device:
        return False, "기기 번호(Android ID / iOS Vendor ID)가 없습니다."
    current = mobile_hwid(device, platform)
    normalized = normalize_key(license_key)
    expected = issue_key_for_mobile_hwid(current)
    if not hmac.compare_digest(normalized, expected):
        return False, "잘못된 라이선스 키이거나 이 스마트폰용 키가 아닙니다."

    payload = load_mobile_activation()
    if payload:
        if str(payload.get("hwid") or "") != current:
            return False, "이 라이선스는 최초 등록된 1대의 스마트폰에서만 사용할 수 있습니다."
        if str(payload.get("key_fp") or "") not in ("", _fingerprint_key(normalized)):
            return False, "이미 다른 라이선스 키로 이 기기가 등록되어 있습니다."
        return True, "이미 이 스마트폰에 라이선스가 등록되어 있습니다."

    save_mobile_activation(normalized, current)
    return True, "라이선스가 이 스마트폰에 등록되었습니다."


def verify_or_activate_mobile(license_key, device_id, platform=""):
    ok, message = verify_mobile_license(device_id, platform)
    if ok:
        current = mobile_hwid(device_id, platform)
        expected = issue_key_for_mobile_hwid(current)
        if not hmac.compare_digest(normalize_key(license_key), expected):
            return False, "잘못된 라이선스 키이거나 이 스마트폰용 키가 아닙니다."
        return True, message
    return activate_mobile_license(license_key, device_id, platform)


def require_license(interactive=True):
    ok, message = verify_saved_license()
    if ok:
        return True
    if not interactive:
        raise RuntimeError(
            "라이선스 인증이 필요합니다. 앱에서 라이선스 키를 등록하세요. ({})".format(message)
        )
    print("=" * 52)
    print(" AI 숏폼 & 릴스 자동 제작기  ·  라이선스 인증")
    print("=" * 52)
    print("이 컴퓨터 머신 코드: {}".format(machine_code()))
    print("HWID: {}".format(formatted_hwid()))
    print("발급받은 라이선스 키를 입력하세요.")
    try:
        entered = input("License Key: ").strip()
    except EOFError:
        entered = ""
    ok, message = activate_license(entered)
    if not ok:
        raise SystemExit("실행이 차단되었습니다. " + message)
    print(message)
    return True


def parse_hwid_arg(text):
    compact = "".join(ch for ch in (text or "") if ch.isalnum()).upper()
    return compact


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--issue", "issue"):
        if len(argv) > 1:
            hwid = parse_hwid_arg(argv[1])
            if len(hwid) != 64:
                print("고객 화면의 HWID 전체(64자)를 넣어 주세요.")
                return 1
            print(issue_key_for_hwid(hwid))
            return 0
        print("HWID: {}".format(get_hwid()))
        print("머신 코드: {}".format(machine_code()))
        print("이 컴퓨터용 License Key: {}".format(issue_key_for_this_pc()))
        return 0
    if argv and argv[0] in ("--issue-mobile", "issue-mobile"):
        if len(argv) < 2:
            print("사용법: python license_lock.py --issue-mobile <AndroidID또는VendorID> [android|ios]")
            return 1
        platform = argv[2] if len(argv) > 2 else ""
        print(issue_key_for_mobile_device(argv[1], platform))
        return 0
    require_license(interactive=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
