"""数据备份：把核心业务库（账单/卡片/记忆/日程）复制成带时间戳的备份。

用法（在 backend 目录下）：
    python scripts/backup.py              # 只备份 personal_copilot.db（核心数据，小）
    python scripts/backup.py --with-ckpt  # 同时备份 checkpoints.db（对话历史，可能很大）

备份产出到 backend/data/backups/ 下，文件名带时间戳，可安全多次执行。
checkpoints.db（对话历史）默认不备份：它随对话快速增长（可达数百 MB），
且核心资产（账单）都在 personal_copilot.db 里。需要恢复对话历史时才加 --with-ckpt。
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / 'data'
BACKUP_DIR = DATA_DIR / 'backups'

CORE_DB = 'personal_copilot.db'
CKPT_DB = 'checkpoints.db'
AUTH_SECRET = 'auth_secret'


def backup_file(name: str) -> Path | None:
    src = DATA_DIR / name
    if not src.exists():
        print(f'  [跳过] {name} 不存在')
        return None
    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = BACKUP_DIR / f'{name}.{ts}'
    shutil.copy2(src, dst)
    size_mb = dst.stat().st_size / 1024 / 1024
    print(f'  [已备份] {name} -> {dst.name}（{size_mb:.2f} MB）')
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description='备份 PersonalCopilot 数据')
    parser.add_argument('--with-ckpt', action='store_true',
                        help='同时备份 checkpoints.db（对话历史，可能很大）')
    args = parser.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    print('备份目录：', BACKUP_DIR)
    backup_file(CORE_DB)
    backup_file(AUTH_SECRET)
    if args.with_ckpt:
        backup_file(CKPT_DB)

    # 列出当前所有备份
    backups = sorted(BACKUP_DIR.glob('*.db.*'))
    print(f'\n当前共有 {len(backups)} 个备份文件。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
