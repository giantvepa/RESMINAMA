"""Электронная подпись (Ф12): detached PKCS#7/CMS подписи файлов, stdlib-only.

Подпись формируется внешним криптографическим контуром:
  openssl smime -sign -binary -in <файл> -signer <pem> -inkey <pem> [-certfile <цепочка>] \
      -outform DER -out <sig>
Проверка — `openssl smime -verify` (для самоподписанных сертификатов с флагом -no_check_time
при проверке против доверенного файла сертификата). Интеграция с НКЦ/E-IMZO добавляется
на Фазе B (см. docs/PLAN_RESMINAMA_NA_BASE_ESASY.md).

Модуль не хранит приватные ключи: ключи лежат в data/keys/<username>/ (создаются
сотрудником через UI или openssl вручную). Сервер только запрашивает парольную фразу
на время операции и вызывает openssl подprocess-ом с ограничением по времени.
"""
from __future__ import annotations
import hashlib, os, re, shutil, subprocess, time
from pathlib import Path

SUPPORTED = ('pdf', 'docx')
MAX_FILE = 20 * 1024 * 1024
TIMEOUT = 60


class SignError(Exception):
    pass


def _decode_dn(value: str) -> str:
    """\u041d\u043e\u0440\u043c\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u044f DN \u0438\u0437 \u0432\u044b\u0432\u043e\u0434\u0430 openssl: 'CN = X' -> 'CN=X';
    \u0440\u0430\u0441\u043f\u0430\u043a\u043e\u0432\u044b\u0432\u0430\u0435\u0442 escape-\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438 \u0444\u043e\u0440\u043c\u0430\u0442\u043e\u0432
    '\\\\xC3\\\\x90' (esc_2253), '\\\\C3\\\\x90' (esc_msb/esc_utf8_string) \u0438 '\\\\U0414' (\u043a\u043e\u0434\u043e\u0442\u043e\u0447\u043a\u0438 UTF8String)."""
    value = re.sub(r'\s*=\s*', '=', value)

    def unescape_hex(m):
        hexes = re.findall(r'\\(?:x)?([0-9A-Fa-f]{2})', m.group(0))
        try:
            return bytes.fromhex(''.join(hexes)).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            try:
                return bytes.fromhex(''.join(hexes)).decode('latin-1')
            except ValueError:
                return m.group(0)

    value = re.sub(r'(?:\\x[0-9A-Fa-f]{2}|\\[0-9A-Fa-f]{2}){2,}', unescape_hex, value)
    value = re.sub(r'\\U([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), value)
    return value


def _split_dn_fields(value: str):
    r"""Разбивает DN по запятым вне escape-последовательностей \xNN / \NN."""
    fields, buf, i = [], [], 0
    while i < len(value):
        ch = value[i]
        if ch == '\\' and i + 2 < len(value):
            buf.append(value[i:i + 3]); i += 3; continue
        if ch == ',':
            fields.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    fields.append(''.join(buf))
    return fields


def _decode_fp(value: str) -> str:
    """openssl 3.x выводит 'SHA256 Fingerprint', openssl 1.x — 'sha256Fingerprint'."""
    m = re.search(r'SHA256\s*Fingerprint=(.*)', value, re.I)
    return m.group(1).strip() if m else ''


def _cert_info_raw(exe: str, crt: Path) -> str:
    """subject читаем отдельным запуском с -nameopt esc_msb,sep1 (стабильный \\xx-формат для
    не-ASCII в openssl 1.x/3.x); срок действия и отпечаток — стандартным выводом."""
    info = subprocess.run([exe, 'x509', '-in', str(crt), '-noout', '-subject', '-enddate',
                           '-fingerprint', '-sha256'], capture_output=True, timeout=TIMEOUT
                          ).stdout.decode('latin-1')
    if 'subject=' in info:
        name = subprocess.run([exe, 'x509', '-in', str(crt), '-noout', '-nameopt', 'esc_2253', '-subject'],
                              capture_output=True, timeout=TIMEOUT).stdout.decode('latin-1', errors='replace')
        m = re.search(r'subject=(.*)', name)
        if m:
            fields = [_decode_dn(f) for f in _split_dn_fields(m.group(1).strip())]
            info = re.sub(r'subject=.*', lambda _: 'subject=' + ','.join(fields), info)
    return info


def _openssl():
    exe = shutil.which('openssl')
    if not exe:
        raise SignError('Не найден openssl на сервере. Установите OpenSSL для работы ЭЦП.')
    return exe


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def key_dir(keys_root: Path, username: str) -> Path:
    raw = str(username)
    # запрещаем path traversal ДО санитизации: иначе '../..' молча превращается в допустимое имя
    if '..' in raw or '/' in raw or '\\' in raw:
        raise SignError('Некорректное имя пользователя')
    user = re.sub(r'[^a-z0-9._-]', '', raw.lower())[:64]
    if '/' in user or '\\' in user or not user or set(user) <= {'.', '_'}:
        raise SignError('Некорректное имя пользователя')
    d = (keys_root / user).resolve()
    root = Path(keys_root).resolve()
    if not d.is_relative_to(root):
        raise SignError('Некорректное имя пользователя')
    return d


def has_certificate(keys_root: Path, username: str) -> bool:
    d = key_dir(keys_root, username)
    return (d / 'signer.crt').exists() and (d / 'signer.key').exists()


def generate_selfsigned(keys_root: Path, username: str, common_name: str) -> dict:
    """Тестовый/внутренний сабжект: самоподписанный RSA-2048 сертификат (1 год).
    Для промышленной эксплуатации сотрудник загружает ключевой контейнер,
    выпущенный удостоверяющим центром (Фаза B — E-IMZO)."""
    exe = _openssl()
    d = key_dir(keys_root, username)
    if d.exists() and any(d.iterdir()):
        raise SignError('Ключевой контейнер уже существует. Сначала удалите его в профиле.')
    d.mkdir(parents=True, exist_ok=False)
    os.chmod(d, 0o700)
    cn = re.sub(r'[^\w .@()-]', '', str(common_name))[:120] or str(username)
    # CN пишем как UTF8String (флаг -utf8): RFC2253-escape \xx в -subj openssl 3.x не
    # раскодировает (строит ASCII-имя из литералов и падает на maxsize=64).
    proc = subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
                           '-days', '365', '-utf8', '-keyout', str(d / 'signer.key'),
                           '-out', str(d / 'signer.crt'),
                           '-subj', f'/CN={cn}'],
                          capture_output=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        raise SignError('Не удалось создать ключевой контейнер: ' + proc.stderr.decode(errors='replace')[-300:])
    os.chmod(d / 'signer.key', 0o600)
    info = _cert_info_raw(exe, d / 'signer.crt')
    subject = re.search(r'subject=(.*)', info)
    expires = re.search(r'notAfter=(.*)', info)
    fp = _decode_fp(info)
    subject_text = _decode_dn(subject.group(1).strip()) if subject else cn
    return {'cn': cn,
            'subject': subject_text,
            'expiresAt': _parse_openssl_date(expires.group(1).strip()) if expires else '',
            'fingerprint': fp.replace(':', '').lower()}


def _parse_openssl_date(value: str) -> str:
    from datetime import datetime
    try:
        return datetime.strptime(value, '%b %d %H:%M:%S %Y %Z').isoformat() + 'Z'
    except ValueError:
        return value


def cert_info(keys_root: Path, username: str) -> dict | None:
    d = key_dir(keys_root, username)
    crt = d / 'signer.crt'
    if not crt.exists():
        return None
    exe = _openssl()
    info = _cert_info_raw(exe, crt)
    subject = re.search(r'subject=(.*)', info)
    expires = re.search(r'notAfter=(.*)', info)
    fp = _decode_fp(info)
    return {'subject': _decode_dn(subject.group(1).strip()) if subject else '',
            'expiresAt': _parse_openssl_date(expires.group(1).strip()) if expires else '',
            'fingerprint': fp.replace(':', '').lower(),
            'selfSigned': True}


def remove_keys(keys_root: Path, username: str):
    shutil.rmtree(key_dir(keys_root, username), ignore_errors=True)


def sign_file(keys_root: Path, username: str, file_path: Path, sig_path: Path) -> dict:
    exe = _openssl()
    d = key_dir(keys_root, username)
    key, crt = d / 'signer.key', d / 'signer.crt'
    if not key.exists() or not crt.exists():
        raise SignError('У сотрудника нет ключевого контейнера. Создайте или загрузите его в профиле.')
    chain = [x for x in ('ca-chain.crt',) if (d / x).exists()]
    cmd = [exe, 'smime', '-sign', '-binary', '-in', str(file_path),
           '-signer', str(crt), '-inkey', str(key), '-outform', 'DER', '-out', str(sig_path)]
    if chain:
        cmd += ['-certfile', str(d / chain[0])]
    proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
    if proc.returncode != 0:
        sig_path.unlink(missing_ok=True)
        raise SignError('Ошибка подписания: ' + proc.stderr.decode(errors='replace')[-300:])
    return {'algorithm': 'RSA-SHA256/PKCS#7(CMS,detached)', 'fileSha256': sha256_file(file_path)}


def verify_file(keys_root: Path, username: str, file_path: Path, sig_path: Path) -> dict:
    """Проверка отсоединённой подписи против сертификата владельца подписи."""
    exe = _openssl()
    crt = key_dir(keys_root, username) / 'signer.crt'
    if not crt.exists():
        raise SignError('Нет сертификата для проверки. Проверьте подпись вручную.')
    # подпись в DER (см. sign_file) -> smime требует явный -inform DER
    proc = subprocess.run([exe, 'smime', '-verify', '-binary', '-inform', 'DER',
                           '-in', str(sig_path),
                           '-content', str(file_path), '-CAfile', str(crt),
                           '-purpose', 'any', '-check_ss_sig', '-no_check_time',
                           '-out', os.devnull], capture_output=True, timeout=TIMEOUT)
    valid = proc.returncode == 0
    return {'valid': valid, 'detail': proc.stderr.decode(errors='replace')[-200:] if not valid else 'signature verified'}
