#!/bin/sh
# Run as root after the hosting provider has allowed this domain.
set -eu
umask 077
[ "$(id -u)" -eq 0 ] || { echo 'Run this script as root.' >&2; exit 1; }
domain=ziqiangkexie.cn
site_config=/www/server/panel/vhost/nginx/ziqiang.conf
backup_config=/root/ziqiang-backups/nginx-before-https-$(date +%Y%m%d-%H%M%S).conf
mkdir -p /var/lib/letsencrypt /root/ziqiang-backups
certbot certonly --webroot -w /var/lib/letsencrypt \
    -d "$domain" -d "www.$domain" \
    --non-interactive --agree-tos --register-unsafely-without-email
cp "$site_config" "$backup_config"
install -m 644 /opt/ZQbackend/deploy/nginx.conf "$site_config"
if ! nginx -t; then
    cp "$backup_config" "$site_config"
    echo 'HTTPS configuration failed validation; previous configuration restored.' >&2
    exit 1
fi
nginx -s reload
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nnginx -t && nginx -s reload\n' > /etc/letsencrypt/renewal-hooks/deploy/ziqiang-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/ziqiang-nginx
systemctl enable --now certbot.timer
systemctl start ziqiang-backup
echo 'HTTPS enabled. Verify both public domains and run certbot renew --dry-run.'
