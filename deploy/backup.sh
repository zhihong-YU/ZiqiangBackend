#!/bin/sh
set -eu
umask 077
backup_root=/root/ziqiang-backups
backup_stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup_root"
runuser -u postgres -- pg_dump --format=custom ziqiang > "$backup_root/database-$backup_stamp.dump"
tar -czf "$backup_root/media-$backup_stamp.tar.gz" -C /opt/ZQbackend media
tar -czf "$backup_root/config-$backup_stamp.tar.gz" -C /opt/ZQbackend .env deploy
