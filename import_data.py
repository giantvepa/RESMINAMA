#!/usr/bin/env python3
"""Copy an earlier RESMINAMA data directory without changing the source."""
from pathlib import Path
import json
import shutil
import sqlite3
import sys
import tempfile

ROOT=Path(__file__).resolve().parent

def import_data(source:Path,destination:Path):
    source=source.expanduser().resolve()
    if (source/'data'/'resminama.sqlite3').is_file():source=source/'data'
    destination=destination.resolve()
    if source==destination or source in destination.parents or destination in source.parents:
        raise ValueError('Выберите отдельную папку старой версии.')
    if not (source/'resminama.sqlite3').is_file():
        raise ValueError('В выбранной папке не найдена база data/resminama.sqlite3.')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('В новой версии уже есть данные. Перенос отменён: ничего не перезаписано. Распакуйте архив в новую пустую папку и повторите перенос.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.resminama-import-',dir=destination.parent))
    try:
        shutil.copytree(source,staging,dirs_exist_ok=True)
        for name in ['resminama.sqlite3','resminama.sqlite3-wal','resminama.sqlite3-shm']:
            (staging/name).unlink(missing_ok=True)
        with sqlite3.connect((source/'resminama.sqlite3').as_uri()+'?mode=ro',uri=True) as original,sqlite3.connect(staging/'resminama.sqlite3') as copied:
            version=original.execute('PRAGMA user_version').fetchone()[0]
            if version>4:raise ValueError('База создана более новой версией RESMINAMA. Этот архив её не поддерживает.')
            original.backup(copied)
            if copied.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Проверка целостности базы не пройдена.')
            state=json.loads(copied.execute('SELECT data FROM workspace WHERE id=1').fetchone()[0])
            copied.execute('SELECT username,password_hash,role FROM users').fetchall()
            for attachment in state['attachments']:
                path=(staging/'uploads'/attachment['key']).resolve()
                if not path.is_relative_to((staging/'uploads').resolve()) or not path.is_file():
                    raise ValueError('В старой папке отсутствует вложение. Перенос отменён; проверьте комплектность данных и остановите старый сервер.')
        if destination.exists():destination.rmdir()  # only an empty target is allowed
        staging.rename(destination)
        return len(state['documents'])
    except Exception:
        if staging.exists():shutil.rmtree(staging)
        raise

def main():
    print('RESMINAMA 0.3 — перенос данных\n')
    print('Сначала остановите старую и новую версии (Ctrl+C в окне сервера).')
    print('Старая папка останется без изменений. Существующие данные новой версии не перезаписываются.\n')
    source=sys.argv[1] if len(sys.argv)>1 else input('Путь к папке старой версии (например C:\\RESMINAMA_SED_v0.2.0): ').strip().strip('"')
    if not source:
        print('Перенос отменён.');return
    try:
        count=import_data(Path(source),ROOT/'data')
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError) as error:
        print('\nОШИБКА:',error);raise SystemExit(1)
    print(f'\nДанные перенесены. Документов: {count}. Запустите START.bat.')
    print('Логины и пароли остаются прежними. Обновление структуры базы выполнится при запуске.')

if __name__=='__main__':main()
